"""Notifications: a message shown on the clock when an entity enters a state.

An app lives on the clock and is kept up to date. A notification is an event:
it is sent when its entity *enters* the chosen state, and taken off again when
the entity leaves it (if asked to). Nothing is kept on the clock between the
two, so, like apps, a notification leaves no trace in the recorder: it follows
state changes through the event bus without firing events or writing states.

The content is written like an app's: a simple notification has the same fields
(entities, layout, colors, progress bar...) and is rendered by the same
templates; a template notification renders the firmware's own JSON. What makes
it a notification is when it is sent and how long it stays (duration, repeats,
hold, sound).

Notifications are for AWTRIX NG: each carries a name and is dismissed by it, so
it never takes down a notification of another automation. Nothing is kept
between restarts of Home Assistant: the rule watches its entity again at start.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import json
import logging
import re
from typing import Any, Final

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import (
    CALLBACK_TYPE,
    Event,
    EventStateChangedData,
    HomeAssistant,
    callback,
)
from homeassistant.exceptions import TemplateError
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.helpers.template import Template

from .const import (
    APP_NAME_PATTERN,
    CONF_ALIGN,
    CONF_COLOR,
    CONF_COOLDOWN,
    CONF_DISMISS_ON_CLEAR,
    CONF_DURATION,
    CONF_ENTITY_ID,
    CONF_FIRMWARE,
    CONF_FONT,
    CONF_HOLD,
    CONF_ICON,
    CONF_LABEL,
    CONF_LAYOUT,
    CONF_MODE,
    CONF_NOTIFICATION_NAME,
    CONF_REPEAT,
    CONF_SEGMENTS,
    CONF_SOUND,
    CONF_STACK,
    CONF_TEMPLATE,
    CONF_TRIGGER_ENTITY,
    CONF_TRIGGER_STATE,
    DEFAULT_COLOR,
    DEFAULT_NOTIFICATION_DURATION,
    DEFAULT_TRIGGER_STATE,
    FIRMWARE_AWTRIX_NG,
    LAYOUT_TITLE_VALUE,
    LAYOUT_TWO_LINES,
    LINE_ALIGNS,
    MAX_NOTIFICATION_COOLDOWN,
    MAX_NOTIFICATION_DURATION,
    MAX_NOTIFICATION_REPEAT,
    MODE_SIMPLE,
    MODE_TEMPLATE,
    NOTIFICATION_LAYOUTS,
    NOTIFICATION_TEST_DURATION,
    SUBENTRY_TYPE_NOTIFICATION,
    TEXT_FONTS,
    VALUE_FONTS,
)
from .drivers import FirmwareDriver
from .pages import InvalidPagesError, parse_pages
from .templates import ENTITY_ID_RE, InvalidSimpleOptionsError, build_lines_template
from .validation import Invalid, _bool, _color, _number, _segment, _text

_LOGGER = logging.getLogger(__name__)

# Longest trigger state and sound name accepted from the forms.
MAX_STATE = 64
MAX_SOUND = 64

type PublishCallback = Callable[[str, str], Any]


class InvalidNotificationError(ValueError):
    """A notification that cannot be turned into a message for the clock."""


@dataclass(frozen=True, slots=True)
class NotificationConfig:
    """Settings of one notification."""

    name: str
    trigger_entity_id: str
    trigger_state: str
    dismiss_on_clear: bool
    mode: str
    # Simple mode: what an app is made of (entities, layout, colors...).
    content: Mapping[str, Any] = field(default_factory=dict)
    duration: int = DEFAULT_NOTIFICATION_DURATION
    hold: bool = False
    repeat: int = 0
    # Minutes after one is shown during which a new trigger is ignored.
    cooldown: int = 0
    stack: bool = True
    sound: str = ""
    # Template mode.
    template: str = ""


# What belongs to the notification itself, not to what it shows.
NOTIFICATION_KEYS: Final = frozenset(
    {
        CONF_NOTIFICATION_NAME,
        CONF_TRIGGER_ENTITY,
        CONF_TRIGGER_STATE,
        CONF_DISMISS_ON_CLEAR,
        CONF_DURATION,
        CONF_HOLD,
        CONF_COOLDOWN,
        CONF_REPEAT,
        CONF_STACK,
        CONF_SOUND,
    }
)


def config_from_data(data: Mapping[str, Any]) -> NotificationConfig:
    """Read a stored notification (already validated)."""
    return NotificationConfig(
        name=str(data[CONF_NOTIFICATION_NAME]),
        trigger_entity_id=str(data[CONF_TRIGGER_ENTITY]),
        trigger_state=str(data.get(CONF_TRIGGER_STATE, DEFAULT_TRIGGER_STATE)),
        dismiss_on_clear=bool(data.get(CONF_DISMISS_ON_CLEAR, True)),
        mode=str(data.get(CONF_MODE, MODE_SIMPLE)),
        content={k: v for k, v in data.items() if k not in NOTIFICATION_KEYS},
        duration=int(data.get(CONF_DURATION, DEFAULT_NOTIFICATION_DURATION)),
        hold=bool(data.get(CONF_HOLD, False)),
        repeat=int(data.get(CONF_REPEAT, 0)),
        cooldown=int(data.get(CONF_COOLDOWN, 0)),
        stack=bool(data.get(CONF_STACK, True)),
        sound=str(data.get(CONF_SOUND, "") or ""),
        template=str(data.get(CONF_TEMPLATE, "")),
    )


def notification_template(config: NotificationConfig) -> str:
    """The template that renders what a notification shows."""
    if config.mode == MODE_TEMPLATE:
        return config.template
    return build_lines_template(config.content)


def wrap_page(config: NotificationConfig, page: Mapping[str, Any]) -> dict[str, Any]:
    """Turn a page, as an app would show it, into a notification.

    The notification's own settings are added in AWTRIX NG's keys. NG checks
    every key strictly and refuses the whole message for a wrong one, so only
    the keys it documents for notifications are added.
    """
    payload = dict(page)
    payload.update(
        {
            "name": config.name,
            "hold": config.hold,
            "stack": config.stack,
            "durationMs": config.duration * 1000,
        }
    )
    # NG's default (0) means "use the duration".
    if config.repeat > 0:
        payload["repeat"] = config.repeat
    if config.sound:
        payload["sound"] = config.sound
    return payload


def payload_from_template(
    config: NotificationConfig, page: Mapping[str, Any]
) -> dict[str, Any]:
    """The notification of a template item: the JSON the template rendered.

    The template owns every key but the name: it is always the notification's,
    so that it can be dismissed by name.
    """
    payload = dict(page)
    payload["name"] = config.name
    return payload


def parse_first_page(result: Any) -> dict[str, Any] | None:
    """The page a template rendered (None when it rendered nothing)."""
    try:
        pages = parse_pages(result)
    except InvalidPagesError as err:
        raise InvalidNotificationError(str(err)) from err
    return pages[0] if pages else None


def build_message(
    config: NotificationConfig, page: Mapping[str, Any]
) -> dict[str, Any]:
    """The message for a rendered page, by mode."""
    if config.mode == MODE_TEMPLATE:
        return payload_from_template(config, page)
    return wrap_page(config, page)


def render_payload(hass: HomeAssistant, config: NotificationConfig) -> dict[str, Any]:
    """Render the message to send now; raise InvalidNotificationError."""
    try:
        result = Template(notification_template(config), hass).async_render(
            parse_result=True
        )
    except (TemplateError, InvalidSimpleOptionsError) as err:
        raise InvalidNotificationError(str(err)) from err
    page = parse_first_page(result)
    if page is None:
        raise InvalidNotificationError(
            "nothing to show (the entity is unavailable, or hidden by its options)"
        )
    return build_message(config, page)


def serialize(payload: dict[str, Any]) -> str:
    """Serialize a message compactly, keeping non-ASCII characters readable."""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def is_trigger(state: str | None, trigger_state: str) -> bool:
    """True if `state` is the state the notification waits for."""
    return state is not None and state.strip().lower() == trigger_state.strip().lower()


class NotificationRule:
    """Send a notification when an entity enters a state; take it off when it
    leaves, if asked to.

    It offers what `LiveApp` offers to the switches (`enabled`, `async_enable`,
    `async_disable`, `async_remove_from_clock`), so that one switch class
    serves both.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        driver: FirmwareDriver,
        config: NotificationConfig,
        publish: PublishCallback,
    ) -> None:
        """Initialize the notification (nothing runs until it is enabled)."""
        self.hass = hass
        self.driver = driver
        self.config = config
        self._publish_cb = publish
        self._enabled = False
        self._active = False
        self._last_error: str | None = None
        self._clock_error: str | None = None
        self._last_sent: datetime | None = None
        self._unsub: CALLBACK_TYPE | None = None
        self._test_cancel: CALLBACK_TYPE | None = None
        self._listeners: list[Callable[[], None]] = []

    # ------------------------------------------------------------------ state

    @property
    def name(self) -> str:
        """Return the notification's name."""
        return self.config.name

    @property
    def enabled(self) -> bool:
        """Return True while the notification is watching its entity."""
        return self._enabled

    @property
    def active(self) -> bool:
        """Return True while the notification is believed to be on the clock."""
        return self._active

    @property
    def last_error(self) -> str | None:
        """Return why the last notification could not be sent, if it could not."""
        return self._last_error or self._clock_error

    @property
    def clock_error(self) -> str | None:
        """Return the clock's refusal of the last notification, if it refused."""
        return self._clock_error

    @callback
    def async_set_clock_error(self, error: str | None) -> None:
        """Record the clock's answer to the last notification (None: accepted)."""
        if error != self._clock_error:
            self._clock_error = error
            self._notify()

    @property
    def last_sent(self) -> datetime | None:
        """Return when the last notification was sent."""
        return self._last_sent

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> CALLBACK_TYPE:
        """Call `listener` whenever the notification's status changes."""
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
        """Start watching the entity."""
        if self._enabled:
            return
        self._enabled = True
        self._unsub = async_track_state_change_event(
            self.hass, [self.config.trigger_entity_id], self._on_state
        )
        self._notify()

    async def async_disable(self, *, remove: bool) -> None:
        """Stop watching; take the notification off the clock if asked to."""
        was_enabled = self._enabled
        self._enabled = False
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
        if remove and was_enabled:
            await self.async_remove_from_clock()
        if was_enabled:
            self._notify()

    async def async_update_config(self, config: NotificationConfig) -> None:
        """Use new settings; the watching restarts if it was running."""
        was_enabled = self._enabled
        if was_enabled:
            await self.async_disable(remove=False)
        self.config = config
        if was_enabled:
            await self.async_enable()

    async def async_remove_from_clock(self) -> None:
        """Take the notification off the clock."""
        if self._test_cancel is not None:
            self._test_cancel()
            self._test_cancel = None
        # Dismissed by name: harmless when it is not shown.
        await self._async_dismiss()
        self._active = False

    async def async_shutdown(self) -> None:
        """Release everything without touching the clock."""
        await self.async_disable(remove=False)
        if self._test_cancel is not None:
            self._test_cancel()
            self._test_cancel = None

    # ---------------------------------------------------------------- events

    @callback
    def _on_state(self, event: Event[EventStateChangedData]) -> None:
        new = event.data["new_state"]
        old = event.data["old_state"]
        if new is None:
            return
        # A state that was not there, or not known yet (Home Assistant starting,
        # an integration reloading), is not a change: nothing is shown for it.
        was_known = old is not None and old.state not in (
            STATE_UNAVAILABLE,
            STATE_UNKNOWN,
        )
        entering = (
            was_known
            and is_trigger(new.state, self.config.trigger_state)
            and not is_trigger(old.state, self.config.trigger_state)
        )
        leaving = (
            old is not None
            and is_trigger(old.state, self.config.trigger_state)
            and not is_trigger(new.state, self.config.trigger_state)
        )
        if entering and self._cooling_down():
            _LOGGER.debug("Notification '%s' ignored: shown too recently", self.name)
        elif entering:
            self.hass.async_create_task(
                self.async_send(), f"awtrix_studio notification {self.name}"
            )
        elif leaving and self.config.dismiss_on_clear:
            # Dismissed by name, so a notification sent before Home Assistant
            # restarted is taken off too.

            self.hass.async_create_task(
                self.async_remove_from_clock(),
                f"awtrix_studio dismiss {self.name}",
            )

    def _cooling_down(self) -> bool:
        """True while a notification shown recently keeps a new one away."""
        if self.config.cooldown <= 0 or self._last_sent is None:
            return False
        waited = datetime.now().astimezone() - self._last_sent
        return waited < timedelta(minutes=self.config.cooldown)

    # -------------------------------------------------------------- publishing

    async def async_send(self) -> str | None:
        """Send the notification now; return why it was not sent, if it was not."""
        topic = self.driver.notify_topic
        if topic is None:
            self._set_error("this firmware has no notifications")
            return self._last_error
        try:
            payload = render_payload(self.hass, self.config)
        except InvalidNotificationError as err:
            self._set_error(str(err))
            _LOGGER.warning("Notification '%s': %s", self.name, err)
            return self._last_error
        error = await self._publish_cb(topic, serialize(payload))
        if error is not None:
            self._set_error(error)
            return error
        self._set_error(None)
        self._active = True
        self._last_sent = datetime.now().astimezone()
        self._notify()
        return None

    async def async_test(self) -> str | None:
        """Send the notification, then take it off after a few seconds.

        A held notification would otherwise stay until someone dismisses it.
        """
        error = await self.async_send()
        if error is not None:
            return error
        if self._test_cancel is not None:
            self._test_cancel()

        async def _dismiss(_now: datetime) -> None:
            self._test_cancel = None
            await self.async_remove_from_clock()
            self._notify()

        self._test_cancel = async_call_later(
            self.hass, NOTIFICATION_TEST_DURATION, _dismiss
        )
        return None

    async def _async_dismiss(self) -> None:
        message = self.driver.dismiss_message(self.name)
        if message is not None:
            await self._publish_cb(*message)

    @callback
    def _set_error(self, error: str | None) -> None:
        if error != self._last_error:
            self._last_error = error
            self._notify()


# ------------------------------------------------------------- validation

SOUND_RE: Final = re.compile(r"^[A-Za-z0-9_.-]{0,64}$")
MAX_NOTIFICATION_TEMPLATE: Final = 20_000


def _string(data: Mapping[str, Any], key: str, limit: int, default: str = "") -> str:
    value = data.get(key, default)
    if value is None:
        return default
    if not isinstance(value, str) or len(value) > limit:
        raise Invalid(key, "invalid_value")
    return value.strip()


def _integer(
    data: Mapping[str, Any], key: str, low: int, high: int, default: int
) -> int:
    number = _number(data, key, low, high)
    return default if number is None else round(number)


def _trigger_entity(data: Mapping[str, Any]) -> str:
    value = data.get(CONF_TRIGGER_ENTITY)
    if not isinstance(value, str) or not ENTITY_ID_RE.fullmatch(value):
        raise Invalid(CONF_TRIGGER_ENTITY, "invalid_entity")
    return value


def _line(
    data: Mapping[str, Any], layout: str, index: int, *, field: str = ""
) -> dict[str, Any]:
    """One line: a text and/or an entity, with how they are written."""
    try:
        if data.get(CONF_ENTITY_ID):
            clean = _segment(data)
        else:
            clean = {
                CONF_ENTITY_ID: "",
                CONF_LABEL: _text(data, CONF_LABEL),
                CONF_COLOR: _color(data, CONF_COLOR, list(DEFAULT_COLOR)),
            }
        font = data.get(CONF_FONT)
        if font not in (None, ""):
            fonts = TEXT_FONTS
            if not (layout == LAYOUT_TITLE_VALUE and index == 0):
                fonts = (*VALUE_FONTS, *TEXT_FONTS)
            if font not in fonts:
                raise Invalid(CONF_FONT, "invalid_value")
            clean[CONF_FONT] = font
        align = data.get(CONF_ALIGN)
        if align not in (None, ""):
            if align not in LINE_ALIGNS:
                raise Invalid(CONF_ALIGN, "invalid_value")
            if align != "center":
                clean[CONF_ALIGN] = align
    except Invalid as err:
        raise Invalid(f"{field}{err.field}", err.key, err.detail) from err
    return clean


def _content(data: Mapping[str, Any], *, preview: bool) -> dict[str, Any]:
    """What a simple notification shows: one or two lines and an icon."""
    layout = data.get(CONF_LAYOUT) or ""
    if layout and layout not in NOTIFICATION_LAYOUTS:
        raise Invalid(CONF_LAYOUT, "invalid_value")
    wanted = 2 if layout in (LAYOUT_TWO_LINES, LAYOUT_TITLE_VALUE) else 1
    extra = data.get(CONF_SEGMENTS) or []
    if not isinstance(extra, list) or len(extra) != wanted - 1:
        raise Invalid(CONF_SEGMENTS, "invalid_value")
    lines = [_line(data, layout, 0)]
    for index, line in enumerate(extra, 1):
        if not isinstance(line, Mapping):
            raise Invalid(f"{CONF_SEGMENTS}.{index - 1}", "invalid_value")
        lines.append(_line(line, layout, index, field=f"{CONF_SEGMENTS}.{index - 1}."))
    if not preview and not any(
        line[CONF_ENTITY_ID] or line.get(CONF_LABEL) for line in lines
    ):
        raise Invalid(CONF_LABEL, "line_empty")
    clean: dict[str, Any] = {**lines[0], CONF_ICON: _text(data, CONF_ICON)}
    if len(lines) > 1:
        clean[CONF_SEGMENTS] = lines[1:]
    if layout:
        clean[CONF_LAYOUT] = layout
    return clean


def normalize_notification(
    hass: HomeAssistant,
    entry: ConfigEntry,
    data: Mapping[str, Any],
    subentry_id: str | None,
    *,
    preview: bool = False,
) -> dict[str, Any]:
    """Return the data of a notification to store, or raise Invalid.

    A new notification's name picks the topic it can be dismissed by, so it
    is checked and cannot be changed afterwards. In preview, what is missing
    is tolerated: the form is still being filled in.
    """
    if entry.data[CONF_FIRMWARE] != FIRMWARE_AWTRIX_NG:
        raise Invalid(CONF_NOTIFICATION_NAME, "notifications_ng_only")
    if subentry_id is not None:
        name = entry.subentries[subentry_id].data[CONF_NOTIFICATION_NAME]
    else:
        name = _string(data, CONF_NOTIFICATION_NAME, 32)
        if not re.fullmatch(APP_NAME_PATTERN, name):
            if not preview:
                raise Invalid(CONF_NOTIFICATION_NAME, "invalid_app_name")
            name = name or "preview"
        elif any(
            other.subentry_type == SUBENTRY_TYPE_NOTIFICATION
            and other.data.get(CONF_NOTIFICATION_NAME) == name
            for other in entry.subentries.values()
        ):
            raise Invalid(CONF_NOTIFICATION_NAME, "app_name_exists")

    mode = data.get(CONF_MODE, MODE_SIMPLE)
    if mode not in (MODE_SIMPLE, MODE_TEMPLATE):
        raise Invalid(CONF_MODE, "invalid_value")
    clean: dict[str, Any] = {
        CONF_MODE: mode,
        CONF_NOTIFICATION_NAME: name,
        CONF_TRIGGER_ENTITY: _trigger_entity(data),
        CONF_TRIGGER_STATE: _string(
            data, CONF_TRIGGER_STATE, MAX_STATE, DEFAULT_TRIGGER_STATE
        )
        or DEFAULT_TRIGGER_STATE,
        CONF_DISMISS_ON_CLEAR: _bool(data, CONF_DISMISS_ON_CLEAR, True),
        CONF_COOLDOWN: _integer(data, CONF_COOLDOWN, 0, MAX_NOTIFICATION_COOLDOWN, 0),
    }

    if mode == MODE_TEMPLATE:
        template = data.get(CONF_TEMPLATE)
        if (
            not isinstance(template, str)
            or not template.strip()
            or len(template) > MAX_NOTIFICATION_TEMPLATE
        ):
            raise Invalid(CONF_TEMPLATE, "invalid_template", "empty or too long")
        clean[CONF_TEMPLATE] = template
        if not preview:
            try:
                Template(template, hass).ensure_valid()
            except TemplateError as err:
                raise Invalid(CONF_TEMPLATE, "invalid_template", str(err)) from err
        return clean

    clean.update(_content(data, preview=preview))

    sound = _string(data, CONF_SOUND, MAX_SOUND)
    if not SOUND_RE.fullmatch(sound):
        raise Invalid(CONF_SOUND, "invalid_value")
    clean.update(
        {
            CONF_DURATION: _integer(
                data,
                CONF_DURATION,
                1,
                MAX_NOTIFICATION_DURATION,
                DEFAULT_NOTIFICATION_DURATION,
            ),
            CONF_HOLD: _bool(data, CONF_HOLD, False),
            CONF_REPEAT: _integer(data, CONF_REPEAT, 0, MAX_NOTIFICATION_REPEAT, 0),
            CONF_STACK: _bool(data, CONF_STACK, True),
            CONF_SOUND: sound,
        }
    )
    return clean
