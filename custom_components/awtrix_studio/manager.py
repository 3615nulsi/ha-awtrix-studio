"""Per-clock coordination: MQTT subscriptions shared by all apps of a clock."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta
import logging
from typing import Any

import aiohttp
from homeassistant.components import mqtt
from homeassistant.config_entries import ConfigSubentry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_call_later, async_track_time_interval

from .clock_files import ClockFiles
from .const import (
    CONF_APP_NAME,
    CONF_CYCLE_ONLY_ON_SCREEN,
    CONF_HIDE_WHEN_EMPTY,
    CONF_INDICATOR,
    CONF_LIFETIME,
    CONF_MODE,
    CONF_PAGE_INTERVAL,
    CONF_TEMPLATE,
    DEFAULT_CYCLE_ONLY_ON_SCREEN,
    DEFAULT_HIDE_WHEN_EMPTY,
    DEFAULT_LIFETIME,
    DEFAULT_PAGE_INTERVAL,
    MODE_SIMPLE,
    SUBENTRY_TYPE_APP,
    SUBENTRY_TYPE_INDICATOR,
)
from .display import CONF_DISPLAY
from .drivers import DisplayInfo, FirmwareDriver, LoopState, Screen
from .icons import IconStore
from .live_app import AppConfig, LiveApp
from .loop import clean_order, desired_loop
from .templates import (
    InvalidSimpleOptionsError,
    build_app_template,
    build_indicator_template,
)

MANAGED_SUBENTRY_TYPES = (SUBENTRY_TYPE_APP, SUBENTRY_TYPE_INDICATOR)

# How often the screen mirror asks the clock for its screen, while a panel
# is watching. Nothing is requested when no one is.
SCREEN_REFRESH = timedelta(seconds=1)
# How long a "test on the clock" stays before the real content comes back.
TEST_DURATION = 15.0
# How long after an app (re)appears the loop order is checked: AWTRIX puts it
# at the end of the loop.
ORDER_CHECK_DELAY = 2.0
# How often the loop order is checked while one is set, for apps pushed by
# something else.
ORDER_CHECK_INTERVAL = timedelta(seconds=60)

_LOGGER = logging.getLogger(__name__)


def subentry_template(subentry: ConfigSubentry, firmware: str) -> str:
    """Return the template of an app or indicator, generating it in simple mode."""
    data = subentry.data
    if data.get(CONF_MODE) != MODE_SIMPLE:
        return data[CONF_TEMPLATE]
    try:
        if subentry.subentry_type == SUBENTRY_TYPE_INDICATOR:
            return build_indicator_template(data, firmware)
        return build_app_template(data, firmware)
    except InvalidSimpleOptionsError as err:
        _LOGGER.error("Cannot build %s: %s", subentry.title, err)
        return "{}"


def subentry_topic(driver: FirmwareDriver, subentry: ConfigSubentry) -> str:
    """Return the topic an app or indicator publishes to."""
    if subentry.subentry_type == SUBENTRY_TYPE_INDICATOR:
        return driver.indicator_topic(subentry.data[CONF_INDICATOR])
    return driver.app_topic(subentry.data[CONF_APP_NAME])


def app_config_from_subentry(subentry: ConfigSubentry, firmware: str) -> AppConfig:
    """Build the engine configuration of an app or indicator."""
    data = subentry.data
    if subentry.subentry_type == SUBENTRY_TYPE_INDICATOR:
        return AppConfig(
            name=f"indicator{data[CONF_INDICATOR]}",
            template=subentry_template(subentry, firmware),
            page_interval=DEFAULT_PAGE_INTERVAL,
            cycle_only_on_screen=True,
            hide_when_empty=True,
            lifetime=0,
            single_page=True,
        )
    simple = data.get(CONF_MODE) == MODE_SIMPLE
    return AppConfig(
        name=data[CONF_APP_NAME],
        template=subentry_template(subentry, firmware),
        page_interval=float(data.get(CONF_PAGE_INTERVAL, DEFAULT_PAGE_INTERVAL)),
        cycle_only_on_screen=bool(
            data.get(CONF_CYCLE_ONLY_ON_SCREEN, DEFAULT_CYCLE_ONLY_ON_SCREEN)
        ),
        hide_when_empty=simple
        or bool(data.get(CONF_HIDE_WHEN_EMPTY, DEFAULT_HIDE_WHEN_EMPTY)),
        lifetime=int(data.get(CONF_LIFETIME, DEFAULT_LIFETIME)),
        display=dict(data.get(CONF_DISPLAY) or {}),
    )


class ClockManager:
    """Own the apps of one clock and the topics they share."""

    def __init__(
        self, hass: HomeAssistant, driver: FirmwareDriver, icons: IconStore
    ) -> None:
        """Initialize the manager."""
        self.hass = hass
        self.driver = driver
        self.icons = icons
        self.files = ClockFiles(hass, driver)
        # Keyed by config subentry id.
        self.apps: dict[str, LiveApp] = {}
        self._unsubscribers: list[Callable[[], None]] = []
        self._current_app: str | None = None
        self._last_uptime: float | None = None
        self._listeners: list[Callable[[], None]] = []
        self._screen_listeners: list[Callable[[Screen], None]] = []
        self._screen_unsubscribers: list[Callable[[], None]] = []
        self._test_timers: dict[str, CALLBACK_TYPE] = {}
        # What each app was built from, to tell an order change from an edit.
        self._sources: dict[str, tuple[str, dict[str, Any]]] = {}
        self._app_topics: set[str] = set()
        self._shown_topics: set[str] = set()
        self._order: list[str] = []
        self._loop: list[str] | None = None
        self._order_timer: CALLBACK_TYPE | None = None
        self._order_lock = asyncio.Lock()
        self._order_pending = False
        self._online: bool | None = None
        self._capabilities: dict[str, list[str]] | None = None
        self._display_size: tuple[int, int] | None = None
        self._enlarge: bool | None = None
        # The live app or indicator publishing on each topic.
        self._by_topic: dict[str, LiveApp] = {}

    @property
    def current_app(self) -> str | None:
        """Return the name of the app the clock is showing, if known."""
        return self._current_app

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> CALLBACK_TYPE:
        """Call `listener` when an app changes or the clock switches app."""
        self._listeners.append(listener)

        @callback
        def remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return remove

    @callback
    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener()

    @property
    def prefix(self) -> str:
        """Return the clock's MQTT prefix."""
        return self.driver.prefix

    @property
    def capabilities(self) -> dict[str, list[str]] | None:
        """Return the effects, overlays and palettes the clock offers, if known."""
        return self._capabilities

    @property
    def display(self) -> DisplayInfo:
        """Return the clock's display size, and how large apps are drawn."""
        if self._display_size is None:
            return DisplayInfo()
        width, height = self._display_size
        # A TC002 draws apps on a half-size grid unless told otherwise.
        scale = 2 if height >= 16 and self._enlarge is not False else 1
        return DisplayInfo(width, height, scale)

    @property
    def order(self) -> list[str]:
        """Return the app loop order chosen by the user (may be empty)."""
        return list(self._order)

    @property
    def loop(self) -> list[str] | None:
        """Return the app loop as last read from the clock, if known."""
        return list(self._loop) if self._loop is not None else None

    def built_from(self, subentries: Iterable[ConfigSubentry]) -> bool:
        """Return True if the apps were built from exactly these subentries."""
        current = {
            subentry.subentry_id: (subentry.subentry_type, dict(subentry.data))
            for subentry in subentries
            if subentry.subentry_type in MANAGED_SUBENTRY_TYPES
        }
        return current == self._sources

    @callback
    def add_app(self, subentry: ConfigSubentry) -> LiveApp:
        """Create the live app or indicator for a subentry (not enabled yet)."""
        app = LiveApp(
            self.hass,
            self.driver,
            app_config_from_subentry(subentry, self.driver.firmware),
            self._publish,
            topic=subentry_topic(self.driver, subentry),
        )
        app.async_set_on_screen(app.name == self._current_app)
        app.async_add_listener(self._notify)
        self.apps[subentry.subentry_id] = app
        self._sources[subentry.subentry_id] = (
            subentry.subentry_type,
            dict(subentry.data),
        )
        topic = subentry_topic(self.driver, subentry)
        self._by_topic[topic] = app
        if subentry.subentry_type == SUBENTRY_TYPE_APP:
            self._app_topics.add(topic)
        return app

    async def async_start(self) -> None:
        """Subscribe to the clock's topics."""
        if topic := self.driver.current_app_topic:
            self._unsubscribers.append(
                await mqtt.async_subscribe(self.hass, topic, self._on_current_app)
            )
        if topic := self.driver.status_topic:
            self._unsubscribers.append(
                await mqtt.async_subscribe(self.hass, topic, self._on_status)
            )
        if topic := self.driver.online_topic:
            self._unsubscribers.append(
                await mqtt.async_subscribe(self.hass, topic, self._on_online)
            )
        if topic := self.driver.capabilities_topic:
            self._unsubscribers.append(
                await mqtt.async_subscribe(self.hass, topic, self._on_capabilities)
            )
        if topic := self.driver.settings_topic:
            self._unsubscribers.append(
                await mqtt.async_subscribe(self.hass, topic, self._on_settings)
            )
        for topic in self.driver.result_topics:
            self._unsubscribers.append(
                await mqtt.async_subscribe(self.hass, topic, self._on_result)
            )
        self._unsubscribers.append(
            mqtt.async_subscribe_connection_status(self.hass, self._on_connection)
        )
        self._unsubscribers.append(
            async_track_time_interval(
                self.hass,
                self._async_periodic_order,
                ORDER_CHECK_INTERVAL,
                name=f"awtrix_studio order {self.prefix}",
            )
        )

    async def async_stop(self) -> None:
        """Stop all apps without touching the clock."""
        while self._unsubscribers:
            self._unsubscribers.pop()()
        self._screen_listeners.clear()
        self._stop_screen()
        for cancel in self._test_timers.values():
            cancel()
        self._test_timers.clear()
        if self._order_timer is not None:
            self._order_timer()
            self._order_timer = None
        for app in self.apps.values():
            await app.async_shutdown()

    async def async_send(self, message: tuple[str, str] | None) -> None:
        """Publish a (topic, payload) command, if the firmware has one."""
        if message is not None:
            await self._publish(*message)

    async def async_test(
        self, topic: str, payload: str, restore: LiveApp | None
    ) -> None:
        """Show a payload for a while, then bring the real content back."""
        if cancel := self._test_timers.pop(topic, None):
            cancel()
        await self._publish(topic, payload)

        async def _restore(_now: datetime) -> None:
            self._test_timers.pop(topic, None)
            if restore is not None and restore.enabled:
                await restore.async_republish()
            else:
                await self._publish(topic, self.driver.remove_payload)

        self._test_timers[topic] = async_call_later(self.hass, TEST_DURATION, _restore)

    # ---------------------------------------------------------- screen mirror

    async def async_subscribe_screen(
        self, listener: Callable[[Screen], None]
    ) -> CALLBACK_TYPE:
        """Stream the clock's screen to `listener` while subscribed."""
        self._screen_listeners.append(listener)
        if len(self._screen_listeners) == 1:
            await self._async_start_screen()

        @callback
        def remove() -> None:
            if listener in self._screen_listeners:
                self._screen_listeners.remove(listener)
            if not self._screen_listeners:
                self._stop_screen()

        return remove

    async def _async_start_screen(self) -> None:
        topic = self.driver.screen_topic
        if topic is None or self.driver.screen_request() is None:
            return
        self._screen_unsubscribers.append(
            await mqtt.async_subscribe(self.hass, topic, self._on_screen)
        )

        async def _request(_now: datetime | None = None) -> None:
            await self.async_send(self.driver.screen_request())

        self._screen_unsubscribers.append(
            async_track_time_interval(
                self.hass,
                _request,
                SCREEN_REFRESH,
                name=f"awtrix_studio screen {self.prefix}",
            )
        )
        await _request()

    @callback
    def _stop_screen(self) -> None:
        while self._screen_unsubscribers:
            self._screen_unsubscribers.pop()()

    @callback
    def _on_screen(self, msg: mqtt.ReceiveMessage) -> None:
        payload = msg.payload
        if isinstance(payload, bytes):
            payload = payload.decode(errors="replace")
        screen = self.driver.parse_screen(str(payload))
        if screen is None:
            return
        for listener in list(self._screen_listeners):
            listener(screen)

    async def async_republish_all(self) -> None:
        """Publish every enabled app again."""
        # The clock may have lost its apps: they all count as new.
        self._shown_topics.clear()
        for app in self.apps.values():
            await app.async_republish()

    async def _publish(self, topic: str, payload: str) -> None:
        # Icons from the LaMetric gallery are sent inline, nothing is stored
        # on the clock.
        payload = await self.icons.async_resolve(payload, self.files, self.driver)
        try:
            await mqtt.async_publish(self.hass, topic, payload, qos=0, retain=False)
        except HomeAssistantError as err:
            _LOGGER.debug("Could not publish to %s: %s", topic, err)
            return
        if topic not in self._app_topics:
            return
        if payload == self.driver.remove_payload:
            self._shown_topics.discard(topic)
        elif topic not in self._shown_topics:
            # The clock added the app at the end of its loop.
            self._shown_topics.add(topic)
            self._schedule_order_check()

    # ------------------------------------------------------------ loop order

    @callback
    def set_order(self, order: Any, *, initial: bool = False) -> None:
        """Use a new loop order and apply it soon.

        A clock that stores the order itself already has it when Home
        Assistant starts: it is only sent again when the user changes it.
        """
        self._order = clean_order(order)
        self._order_pending = bool(self._order) and not (
            initial and self.driver.keeps_order
        )
        self._schedule_order_check(0)

    def _order_wanted(self) -> bool:
        if not self._order or not self.driver.can_reorder:
            return False
        return self._order_pending or not self.driver.keeps_order

    @callback
    def _schedule_order_check(self, delay: float = ORDER_CHECK_DELAY) -> None:
        if not self._order_wanted():
            return
        if self._order_timer is not None:
            self._order_timer()
        self._order_timer = async_call_later(self.hass, delay, self._async_order_timer)

    async def _async_order_timer(self, _now: datetime) -> None:
        self._order_timer = None
        await self.async_apply_order()

    async def _async_periodic_order(self, _now: datetime) -> None:
        await self.async_apply_order()

    async def async_refresh_loop(self) -> list[str] | None:
        """Read the app loop from the clock; None if it cannot be reached."""
        state = await self._async_read_loop()
        return state.apps if state is not None else None

    async def _async_read_loop(self) -> LoopState | None:
        path = self.driver.loop_path
        if path is None or self.files.address is None:
            return None
        try:
            state = self.driver.parse_loop(await self.files.async_get_text(path))
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            _LOGGER.debug("Cannot read the app loop of %s: %s", self.prefix, err)
            return None
        loop = state.apps if state is not None else None
        if loop != self._loop:
            self._loop = loop
            self._notify()
        return state

    async def async_apply_order(self) -> None:
        """Rearrange the clock's loop to follow the chosen order, if needed."""
        if not self._order_wanted():
            return
        async with self._order_lock:
            # The loop is read first: AWTRIX 3 drops the apps a reorder leaves
            # out, and AWTRIX NG needs the apps switched off on the clock.
            state = await self._async_read_loop()
            if state is None:
                return
            if self.driver.keeps_order:
                # Apps that are not there yet keep their place for later.
                wanted = desired_loop(state.apps, self._order, keep_absent=True)
            elif not state.apps:
                return
            else:
                wanted = desired_loop(state.apps, self._order)
                if wanted == state.apps:
                    return
            if not await self._async_send_order(wanted, state.disabled):
                return
            self._order_pending = False
            self._loop = desired_loop(state.apps, self._order)
            self._notify()

    async def _async_send_order(self, order: list[str], disabled: list[str]) -> bool:
        if (message := self.driver.order_message(order, disabled)) is not None:
            await self.async_send(message)
            return True
        if (path := self.driver.reorder_path) is None:
            return False
        try:
            await self.files.async_post_json(path, order)
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            _LOGGER.debug("Cannot reorder the apps of %s: %s", self.prefix, err)
            return False
        return True

    @callback
    def _on_current_app(self, msg: mqtt.ReceiveMessage) -> None:
        payload = msg.payload
        if isinstance(payload, bytes):
            payload = payload.decode(errors="replace")
        current = self.driver.parse_current_app(str(payload))
        if current == self._current_app:
            return
        self._current_app = current
        for app in self.apps.values():
            app.async_set_on_screen(app.name == self._current_app)
        self._notify()

    @callback
    def _on_status(self, msg: mqtt.ReceiveMessage) -> None:
        payload = msg.payload
        if isinstance(payload, bytes):
            payload = payload.decode(errors="replace")
        payload = str(payload)
        address = self.driver.parse_address(payload)
        # Animated icons can now be stored on the clock, and the loop order
        # checked.
        republish = address is not None and self.files.set_address(address)
        version = self.driver.parse_version(payload)
        if version is not None and version != self.driver.version:
            # Inline icons depend on the version: send them again in its form.
            self.driver.version = version
            republish = True
        if republish:
            self.hass.async_create_task(
                self.async_republish_all(), f"awtrix_studio republish {self.prefix}"
            )
        uptime = self.driver.parse_uptime(payload)
        if uptime is None:
            return
        rebooted = self._last_uptime is not None and uptime < self._last_uptime
        self._last_uptime = uptime
        if rebooted:
            _LOGGER.debug("Clock %s restarted, publishing apps again", self.prefix)
            self.hass.async_create_task(
                self.async_republish_all(), f"awtrix_studio republish {self.prefix}"
            )

    @callback
    def _on_capabilities(self, msg: mqtt.ReceiveMessage) -> None:
        payload = msg.payload
        if isinstance(payload, bytes):
            payload = payload.decode(errors="replace")
        capabilities = self.driver.parse_capabilities(str(payload))
        size = self.driver.parse_display_size(str(payload))
        changed = False
        if capabilities is not None and capabilities != self._capabilities:
            self._capabilities = capabilities
            changed = True
        if size is not None and size != self._display_size:
            self._display_size = size
            changed = True
        if changed:
            self._notify()

    @callback
    def _on_settings(self, msg: mqtt.ReceiveMessage) -> None:
        payload = msg.payload
        if isinstance(payload, bytes):
            payload = payload.decode(errors="replace")
        enlarge = self.driver.parse_enlarge(str(payload))
        if enlarge is not None and enlarge != self._enlarge:
            self._enlarge = enlarge
            self._notify()

    @callback
    def _on_result(self, msg: mqtt.ReceiveMessage) -> None:
        payload = msg.payload
        if isinstance(payload, bytes):
            payload = payload.decode(errors="replace")
        result = self.driver.parse_result(msg.topic, str(payload))
        if result is None or (app := self._by_topic.get(result[0])) is None:
            return
        if result[1] is not None and result[1] != app.clock_error:
            _LOGGER.warning("The clock refused app '%s': %s", app.name, result[1])
        app.async_set_clock_error(result[1])

    @callback
    def _on_online(self, msg: mqtt.ReceiveMessage) -> None:
        payload = msg.payload
        if isinstance(payload, bytes):
            payload = payload.decode(errors="replace")
        online = self.driver.parse_online(str(payload))
        if online is None:
            return
        came_back = online and self._online is False
        self._online = online
        if came_back:
            # The clock restarted (or lost its connection) and dropped its apps.
            _LOGGER.debug("Clock %s is back online, publishing apps again", self.prefix)
            self.hass.async_create_task(
                self.async_republish_all(), f"awtrix_studio republish {self.prefix}"
            )

    @callback
    def _on_connection(self, connected: bool) -> None:
        if connected:
            self.hass.async_create_task(
                self.async_republish_all(), f"awtrix_studio republish {self.prefix}"
            )
