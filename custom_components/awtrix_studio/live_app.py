"""A live app: one template rendered into AWTRIX pages and kept on the clock.

Indicators use the same engine: a single page published to their own topic.

Design rule: nothing in here touches the event bus or entity states. Rendering
uses template tracking, publishing uses the MQTT client directly, timers use
the event loop. A live app therefore leaves no trace in the recorder, however
often it updates.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.exceptions import TemplateError
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.event import (
    TrackTemplate,
    TrackTemplateResult,
    TrackTemplateResultInfo,
    async_call_later,
    async_track_template_result,
    async_track_time_interval,
)
from homeassistant.helpers.template import Template

from .const import DATA_CHANGE_COOLDOWN
from .display import apply_display
from .drivers import FirmwareDriver
from .pages import InvalidPagesError, parse_pages, serialize_page

_LOGGER = logging.getLogger(__name__)

type PublishCallback = Callable[[str, str], Awaitable[object]]

# Placeholder shown when an app has no page and is not hidden.
BLANK_PAGE = "{}"


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Settings of one live app."""

    name: str
    template: str
    page_interval: float
    cycle_only_on_screen: bool
    hide_when_empty: bool
    lifetime: int
    single_page: bool = False
    # Display options, added to every page unless the page sets them.
    display: Mapping[str, Any] = field(default_factory=dict)
    # Icon given to the pages that name none (template apps).
    icon: str | None = None


class LiveApp:
    """Render, cycle and publish one app."""

    def __init__(
        self,
        hass: HomeAssistant,
        driver: FirmwareDriver,
        config: AppConfig,
        publish: PublishCallback,
        topic: str | None = None,
    ) -> None:
        """Initialize the app (nothing runs until it is enabled)."""
        self.hass = hass
        self.driver = driver
        self.config = config
        self._publish_cb = publish
        self._topic = topic or driver.app_topic(config.name)

        self._enabled = False
        self._on_screen = False
        self._pages: list[str] = []
        self._page_index = 0
        self._last_payload: str | None = None
        self._last_error: str | None = None
        self._clock_error: str | None = None

        self._listeners: list[Callable[[], None]] = []

        self._tracker: TrackTemplateResultInfo | None = None
        self._cancel_cycle: CALLBACK_TYPE | None = None
        self._cancel_keepalive: CALLBACK_TYPE | None = None
        self._debouncer: Debouncer[Any] = Debouncer(
            hass,
            _LOGGER,
            cooldown=DATA_CHANGE_COOLDOWN,
            immediate=True,
            function=self._async_publish_current,
        )

    # ------------------------------------------------------------------ state

    @property
    def name(self) -> str:
        """Return the AWTRIX app name."""
        return self.config.name

    @property
    def enabled(self) -> bool:
        """Return True if the app is running."""
        return self._enabled

    @property
    def page_count(self) -> int:
        """Return the number of non-empty pages currently rendered."""
        return len(self._pages)

    @property
    def is_cycling(self) -> bool:
        """Return True if pages are currently alternating."""
        return self._cancel_cycle is not None

    @property
    def pages(self) -> list[str]:
        """Return the current pages, serialized as published."""
        return list(self._pages)

    @property
    def page_index(self) -> int:
        """Return the index of the page currently published."""
        return self._page_index

    @property
    def on_screen(self) -> bool:
        """Return True if the clock is showing this app."""
        return self._on_screen

    @property
    def last_error(self) -> str | None:
        """Return the last template error, if the template is failing."""
        return self._last_error

    @property
    def clock_error(self) -> str | None:
        """Return why the clock refused the last payload, if it did."""
        return self._clock_error

    @callback
    def async_set_clock_error(self, error: str | None) -> None:
        """Record the clock's answer to the last payload."""
        if error != self._clock_error:
            self._clock_error = error
            self._notify()

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> CALLBACK_TYPE:
        """Call `listener` whenever what the app shows changes."""
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

    # -------------------------------------------------------------- lifecycle

    async def async_enable(self) -> None:
        """Start rendering and publishing."""
        if self._enabled:
            return
        self._enabled = True
        self._tracker = async_track_template_result(
            self.hass,
            [TrackTemplate(Template(self.config.template, self.hass), None)],
            self._on_template_result,
            log_fn=self._log_template_error,
        )
        # The tracker does not report the first render by itself.
        self._tracker.async_refresh()
        self._notify()

        if self.config.lifetime > 0:
            self._cancel_keepalive = async_track_time_interval(
                self.hass,
                self._async_keepalive,
                timedelta(seconds=max(self.config.lifetime / 2, 1)),
                name=f"awtrix_studio keepalive {self.name}",
            )

    async def async_disable(self, *, remove: bool) -> None:
        """Stop the app; remove it from the clock if asked to."""
        was_enabled = self._enabled
        self._enabled = False
        if self._tracker is not None:
            self._tracker.async_remove()
            self._tracker = None
        if self._cancel_keepalive is not None:
            self._cancel_keepalive()
            self._cancel_keepalive = None
        self._stop_cycling()
        self._debouncer.async_cancel()
        self._pages = []
        self._page_index = 0
        if remove and was_enabled:
            await self._async_publish(self.driver.remove_payload, force=True)
        self._last_payload = None
        if was_enabled:
            self._notify()

    async def async_remove_from_clock(self) -> None:
        """Remove the app from the clock, whatever its state."""
        await self._async_publish(self.driver.remove_payload, force=True)
        self._last_payload = None

    async def async_shutdown(self) -> None:
        """Release everything without touching the clock."""
        await self.async_disable(remove=False)
        self._debouncer.async_shutdown()

    async def async_republish(self) -> None:
        """Publish the current page again, even if unchanged."""
        if self._enabled:
            await self._async_publish(self._current_payload(), force=True)

    # ---------------------------------------------------------------- screen

    @callback
    def async_set_on_screen(self, on_screen: bool) -> None:
        """Tell the app whether the clock is currently showing it."""
        if on_screen == self._on_screen:
            return
        self._on_screen = on_screen
        self._update_cycling()
        self._notify()

    # -------------------------------------------------------------- rendering

    @callback
    def _log_template_error(self, level: int, message: str) -> None:
        """Log template errors once per distinct message."""
        if message != self._last_error:
            self._last_error = message
            _LOGGER.log(level, "App '%s': template error: %s", self.name, message)
            self._notify()

    @callback
    def _on_template_result(
        self, event: Event[Any] | None, updates: list[TrackTemplateResult]
    ) -> None:
        """Handle a new render of the template."""
        if not self._enabled or not updates:
            return
        result = updates[-1].result
        if isinstance(result, TemplateError):
            # Keep showing the last good pages.
            self._log_template_error(logging.WARNING, str(result))
            return
        try:
            pages = parse_pages(result)
        except InvalidPagesError as err:
            self._log_template_error(logging.WARNING, f"invalid pages, {err}")
            return
        self._last_error = None
        if not self._enabled:
            return

        if self.config.single_page:
            pages = pages[:1]
        pages = apply_display(
            pages, self.config.display, self.driver.firmware, icon=self.config.icon
        )
        if self.config.lifetime > 0:
            pages = [self.driver.with_lifetime(p, self.config.lifetime) for p in pages]
        self._pages = [serialize_page(p) for p in pages]
        if self._page_index >= len(self._pages):
            self._page_index = 0

        self._debouncer.async_schedule_call()
        self._update_cycling()
        self._notify()

    # ---------------------------------------------------------------- cycling

    def _should_cycle(self) -> bool:
        return (
            self._enabled
            and len(self._pages) > 1
            and (self._on_screen or not self.config.cycle_only_on_screen)
        )

    @callback
    def _update_cycling(self) -> None:
        """Start or stop alternating pages to match the current situation."""
        if self._should_cycle():
            if self._cancel_cycle is None:
                self._schedule_next_page()
            return
        if self._cancel_cycle is None:
            return
        self._stop_cycling()
        # Back to the first page, ready for the next time the app is shown.
        if self._page_index != 0:
            self._page_index = 0
            self._debouncer.async_schedule_call()
            self._notify()

    @callback
    def _schedule_next_page(self) -> None:
        self._cancel_cycle = async_call_later(
            self.hass, self.config.page_interval, self._async_next_page
        )

    @callback
    def _stop_cycling(self) -> None:
        if self._cancel_cycle is not None:
            self._cancel_cycle()
            self._cancel_cycle = None

    async def _async_next_page(self, _now: datetime) -> None:
        """Show the next page and plan the one after."""
        self._cancel_cycle = None
        if not self._should_cycle():
            self._update_cycling()
            return
        self._page_index = (self._page_index + 1) % len(self._pages)
        self._schedule_next_page()
        self._notify()
        await self._async_publish(self._current_payload())

    # ------------------------------------------------------------- publishing

    def _current_payload(self) -> str:
        if self._pages:
            return self._pages[self._page_index]
        if self.config.hide_when_empty:
            return self.driver.remove_payload
        return BLANK_PAGE

    async def _async_publish_current(self) -> None:
        if self._enabled:
            await self._async_publish(self._current_payload())

    async def _async_keepalive(self, _now: datetime) -> None:
        """Refresh the app before the clock's lifetime expires."""
        if self._pages:
            await self._async_publish(self._current_payload(), force=True)

    async def _async_publish(self, payload: str, *, force: bool = False) -> None:
        if not force and payload == self._last_payload:
            return
        self._last_payload = payload
        await self._publish_cb(self._topic, payload)
