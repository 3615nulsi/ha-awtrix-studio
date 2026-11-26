"""Validation shared by the config flows and the panel.

Anything coming from a user is checked here before it is stored, rendered or
published: only known keys are kept, every value is typed and bounded, and
names that end up in MQTT topics are restricted to a safe alphabet.
"""

from __future__ import annotations

from collections.abc import Mapping
import math
import re
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import TemplateError
from homeassistant.helpers.template import Template

from .const import (
    APP_NAME_PATTERN,
    COLOR_GRADIENT,
    COLOR_STATE,
    COLOR_STEPS,
    CONF_ACTIVE_STATE,
    CONF_APP_NAME,
    CONF_ATTRIBUTE,
    CONF_COLOR,
    CONF_COLOR_1,
    CONF_COLOR_2,
    CONF_COLOR_MODE,
    CONF_CYCLE_ONLY_ON_SCREEN,
    CONF_DECIMALS,
    CONF_EFFECT,
    CONF_EFFECT_INTERVAL,
    CONF_ENTITY_ID,
    CONF_FIRMWARE,
    CONF_GRADIENT,
    CONF_GRADIENT_MAX,
    CONF_GRADIENT_MIN,
    CONF_GRADIENT_STOPS,
    CONF_HIDE_WHEN_EMPTY,
    CONF_HIDE_WHEN_ZERO,
    CONF_ICON,
    CONF_ICON_ENTITY,
    CONF_ICON_MIN,
    CONF_ICON_MODE,
    CONF_ICON_STATES,
    CONF_ICON_STEPS,
    CONF_INDICATOR,
    CONF_LABEL,
    CONF_LABEL_COLOR,
    CONF_LAYOUT,
    CONF_LIFETIME,
    CONF_MODE,
    CONF_PAGE_INTERVAL,
    CONF_PROGRESS,
    CONF_PROGRESS_BG,
    CONF_PROGRESS_COLOR,
    CONF_PROGRESS_ENTITY,
    CONF_PROGRESS_MAX,
    CONF_PROGRESS_MIN,
    CONF_SEGMENTS,
    CONF_SEPARATOR,
    CONF_SHOW_UNIT,
    CONF_TEMPLATE,
    CONF_TEXT_FONT,
    CONF_THRESHOLD_1,
    CONF_THRESHOLD_2,
    CONF_UNIT,
    CONF_VALUE_FONT,
    DEFAULT_ACTIVE_STATE,
    DEFAULT_COLOR,
    DEFAULT_CYCLE_ONLY_ON_SCREEN,
    DEFAULT_EFFECT_INTERVAL,
    DEFAULT_HIDE_WHEN_EMPTY,
    DEFAULT_INDICATOR_COLOR,
    DEFAULT_LIFETIME,
    DEFAULT_PAGE_INTERVAL,
    DEFAULT_PROGRESS_BG,
    DEFAULT_PROGRESS_MAX,
    DEFAULT_PROGRESS_MIN,
    DEFAULT_SEPARATOR,
    EFFECT_NONE,
    EFFECTS,
    FIRMWARE_AWTRIX_NG,
    GRADIENT_CUSTOM,
    ICON_BY_STATE,
    ICON_FIXED,
    ICON_MODES,
    INDICATORS,
    LAYOUT_TWO_LINES,
    LAYOUT_VALUE_BAR,
    LAYOUTS,
    MAX_ICON_STATES,
    MAX_ICON_STEPS,
    MAX_LIFETIME,
    MAX_PAGE_INTERVAL,
    MAX_SEGMENTS,
    MAX_SEPARATOR,
    MAX_UNIT,
    MIN_PAGE_INTERVAL,
    MODE_SIMPLE,
    MODE_TEMPLATE,
    SUBENTRY_TYPE_APP,
    SUBENTRY_TYPE_INDICATOR,
    TEXT_FONTS,
    VALUE_FONTS,
)
from .display import CONF_DISPLAY, InvalidDisplayError, normalize_display
from .gradients import InvalidGradientError, resolve_stops, stops_to_data
from .pages import InvalidPagesError, parse_pages
from .templates import (
    ENTITY_ID_RE,
    HEX_COLOR_RE,
    app_color_mode,
    indicator_color_mode,
)

MAX_TEXT = 64
ATTRIBUTE_RE = re.compile(r"^[A-Za-z0-9_]+$")
MAX_TEMPLATE = 20_000


class Invalid(Exception):
    """A value that cannot be accepted, with the error key to show."""

    def __init__(self, field: str, key: str, detail: str = "") -> None:
        """Remember which field failed and why."""
        super().__init__(f"{field}: {key} {detail}".strip())
        self.field = field
        self.key = key
        self.detail = detail


# ------------------------------------------------------------- primitives


def check_template(hass: HomeAssistant, template_str: str) -> tuple[str, str] | None:
    """Render a template once; return (error key, detail) if it fails."""
    template = Template(template_str, hass)
    try:
        template.ensure_valid()
        result = template.async_render(parse_result=True)
    except TemplateError as err:
        return "invalid_template", str(err)
    try:
        parse_pages(result)
    except InvalidPagesError as err:
        return "invalid_pages", str(err)
    return None


def _bool(data: Mapping[str, Any], key: str, default: bool) -> bool:
    value = data.get(key, default)
    return value if isinstance(value, bool) else default


def _text(data: Mapping[str, Any], key: str, default: str = "") -> str:
    value = data.get(key, default)
    if value is None:
        return default
    if not isinstance(value, str):
        raise Invalid(key, "invalid_value")
    return value.strip()[:MAX_TEXT]


def _number(data: Mapping[str, Any], key: str, low: float, high: float) -> float | None:
    value = data.get(key)
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise Invalid(key, "invalid_value")
    try:
        number = float(value)
    except (TypeError, ValueError) as err:
        raise Invalid(key, "invalid_value") from err
    if not math.isfinite(number):
        raise Invalid(key, "invalid_value")
    return max(low, min(high, number))


def _color(
    data: Mapping[str, Any], key: str, default: list[int] | None
) -> list[int] | None:
    value = data.get(key)
    if value is None or value == "":
        return default
    if isinstance(value, str) and HEX_COLOR_RE.fullmatch(value):
        return [int(value[i : i + 2], 16) for i in (1, 3, 5)]
    if (
        isinstance(value, (list, tuple))
        and len(value) == 3
        and all(isinstance(c, int) and not isinstance(c, bool) for c in value)
        and all(0 <= c <= 255 for c in value)
    ):
        return list(value)
    raise Invalid(key, "invalid_color")


def _entity(data: Mapping[str, Any]) -> str:
    value = data.get(CONF_ENTITY_ID)
    if not isinstance(value, str) or not ENTITY_ID_RE.fullmatch(value):
        raise Invalid(CONF_ENTITY_ID, "invalid_entity")
    return value


def _raw_template(data: Mapping[str, Any]) -> str:
    value = data.get(CONF_TEMPLATE)
    if not isinstance(value, str):
        raise Invalid(CONF_TEMPLATE, "invalid_template", "empty")
    if len(value) > MAX_TEMPLATE:
        raise Invalid(CONF_TEMPLATE, "invalid_template", "too long")
    return value


def _template(hass: HomeAssistant, data: Mapping[str, Any]) -> str:
    value = data.get(CONF_TEMPLATE)
    if not isinstance(value, str) or not value.strip():
        raise Invalid(CONF_TEMPLATE, "invalid_template", "empty")
    if len(value) > MAX_TEMPLATE:
        raise Invalid(CONF_TEMPLATE, "invalid_template", "too long")
    if error := check_template(hass, value):
        raise Invalid(CONF_TEMPLATE, error[0], error[1])
    return value


def _gradient(data: Mapping[str, Any]) -> dict[str, Any]:
    """The gradient settings, checked; a custom gradient keeps its stops."""
    try:
        stops = resolve_stops(data)
    except InvalidGradientError as err:
        raise Invalid(err.field, err.key) from err
    if data.get(CONF_GRADIENT) == GRADIENT_CUSTOM:
        return {
            CONF_GRADIENT: GRADIENT_CUSTOM,
            CONF_GRADIENT_STOPS: stops_to_data(stops),
        }
    return {
        CONF_GRADIENT: data[CONF_GRADIENT],
        CONF_GRADIENT_MIN: stops[0][0],
        CONF_GRADIENT_MAX: stops[-1][0],
    }


def _progress(data: Mapping[str, Any]) -> dict[str, Any]:
    """The optional progress bar of a simple app."""
    if not _bool(data, CONF_PROGRESS, False):
        return {CONF_PROGRESS: False}
    clean: dict[str, Any] = {CONF_PROGRESS: True}
    source = data.get(CONF_PROGRESS_ENTITY)
    if source not in (None, ""):
        if not isinstance(source, str) or not ENTITY_ID_RE.fullmatch(source):
            raise Invalid(CONF_PROGRESS_ENTITY, "invalid_entity")
        clean[CONF_PROGRESS_ENTITY] = source
    low = _number(data, CONF_PROGRESS_MIN, -1e9, 1e9)
    high = _number(data, CONF_PROGRESS_MAX, -1e9, 1e9)
    clean[CONF_PROGRESS_MIN] = DEFAULT_PROGRESS_MIN if low is None else low
    clean[CONF_PROGRESS_MAX] = DEFAULT_PROGRESS_MAX if high is None else high
    if clean[CONF_PROGRESS_MAX] <= clean[CONF_PROGRESS_MIN]:
        raise Invalid(CONF_PROGRESS_MAX, "invalid_range")
    if (color := _color(data, CONF_PROGRESS_COLOR, None)) is not None:
        clean[CONF_PROGRESS_COLOR] = color
    clean[CONF_PROGRESS_BG] = _color(data, CONF_PROGRESS_BG, list(DEFAULT_PROGRESS_BG))
    return clean


def _app_colors(data: Mapping[str, Any]) -> dict[str, Any]:
    """How a simple app picks its color: fixed, by steps or by gradient."""
    color_mode = app_color_mode(data)
    clean: dict[str, Any] = {CONF_COLOR_MODE: color_mode}
    if color_mode == COLOR_GRADIENT:
        clean.update(_gradient(data))
    if color_mode != COLOR_STEPS:
        return clean
    for threshold, color in (
        (CONF_THRESHOLD_1, CONF_COLOR_1),
        (CONF_THRESHOLD_2, CONF_COLOR_2),
    ):
        value = _number(data, threshold, -1e9, 1e9)
        if value is None:
            continue
        chosen = _color(data, color, None)
        if chosen is None:
            raise Invalid(color, "color_required")
        clean[threshold] = value
        clean[color] = chosen
    return clean


def _segment(data: Mapping[str, Any]) -> dict[str, Any]:
    """One entity of a simple app: how its value is written, in which color."""
    clean: dict[str, Any] = {
        CONF_ENTITY_ID: _entity(data),
        CONF_LABEL: _text(data, CONF_LABEL),
        CONF_COLOR: _color(data, CONF_COLOR, list(DEFAULT_COLOR)),
        CONF_SHOW_UNIT: _bool(data, CONF_SHOW_UNIT, True),
        CONF_HIDE_WHEN_ZERO: _bool(data, CONF_HIDE_WHEN_ZERO, False),
    }
    # Only kept when typed: the entity's state otherwise.
    if attribute := _text(data, CONF_ATTRIBUTE):
        if not ATTRIBUTE_RE.fullmatch(attribute):
            raise Invalid(CONF_ATTRIBUTE, "invalid_value")
        clean[CONF_ATTRIBUTE] = attribute
    # Only kept when chosen: the label takes the value's color otherwise.
    if (label_color := _color(data, CONF_LABEL_COLOR, None)) is not None:
        clean[CONF_LABEL_COLOR] = label_color
    decimals = _number(data, CONF_DECIMALS, 0, 4)
    if decimals is not None:
        clean[CONF_DECIMALS] = int(decimals)
    # Only kept when typed: the entity's own unit otherwise.
    if unit := _text(data, CONF_UNIT)[:MAX_UNIT]:
        clean[CONF_UNIT] = unit
    clean.update(_app_colors(data))
    return clean


def _icon_rules(data: Mapping[str, Any]) -> dict[str, Any]:
    """How the icon is chosen: by the state of an entity, or by its value.

    The fixed icon (`icon`) is the one for any other case, so it is required
    once there are rules. An app saved before `icon_mode` existed, with a table
    of states, follows its state. Nothing is kept for a fixed icon.
    """
    mode = data.get(CONF_ICON_MODE)
    if mode in (None, ""):
        mode = ICON_BY_STATE if data.get(CONF_ICON_STATES) else ICON_FIXED
    if mode not in ICON_MODES:
        raise Invalid(CONF_ICON_MODE, "invalid_value")
    if mode == ICON_FIXED:
        return {}
    clean: dict[str, Any] = {}
    entity = data.get(CONF_ICON_ENTITY)
    if entity not in (None, ""):
        if not isinstance(entity, str) or not ENTITY_ID_RE.fullmatch(entity):
            raise Invalid(CONF_ICON_ENTITY, "invalid_entity")
        clean[CONF_ICON_ENTITY] = entity
    if mode == ICON_BY_STATE:
        rules: dict[str, Any] = {CONF_ICON_STATES: _icon_states(data)}
    else:
        rules = {CONF_ICON_STEPS: _icon_steps(data)}
    if not next(iter(rules.values())):
        return {}
    if not _text(data, CONF_ICON):
        raise Invalid(CONF_ICON, "icon_default_required")
    return {CONF_ICON_MODE: mode, **clean, **rules}


def _icon_states(data: Mapping[str, Any]) -> dict[str, str]:
    table = data.get(CONF_ICON_STATES)
    if table is None:
        return {}
    if not isinstance(table, Mapping) or len(table) > MAX_ICON_STATES:
        raise Invalid(CONF_ICON_STATES, "invalid_value")
    icons: dict[str, str] = {}
    for state in table:
        if not isinstance(state, str) or not state.strip() or len(state) > MAX_TEXT:
            raise Invalid(CONF_ICON_STATES, "invalid_value")
        if name := _text(table, state):
            icons[state.strip()] = name
    return icons


def _icon_steps(data: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The steps, from the lowest value up: `min` and above shows `icon`."""
    steps = data.get(CONF_ICON_STEPS)
    if steps is None:
        return []
    if not isinstance(steps, list) or len(steps) > MAX_ICON_STEPS:
        raise Invalid(CONF_ICON_STEPS, "invalid_value")
    clean: dict[float, dict[str, Any]] = {}
    for step in steps:
        if not isinstance(step, Mapping):
            raise Invalid(CONF_ICON_STEPS, "invalid_value")
        try:
            low = _number(step, CONF_ICON_MIN, -1e9, 1e9)
            name = _text(step, CONF_ICON)
        except Invalid as err:
            raise Invalid(CONF_ICON_STEPS, "invalid_value") from err
        if low is None:
            raise Invalid(CONF_ICON_STEPS, "invalid_value")
        if not name:
            continue
        if low in clean:
            raise Invalid(CONF_ICON_STEPS, "invalid_value")
        clean[low] = {
            CONF_ICON_MIN: int(low) if low == int(low) else low,
            CONF_ICON: name,
        }
    return [clean[low] for low in sorted(clean)]


def _segments(data: Mapping[str, Any]) -> dict[str, Any]:
    """The next entities of a simple app, and what separates them.

    Errors name the field as `segments.<index>.<field>`.
    """
    extra = data.get(CONF_SEGMENTS)
    if extra in (None, []):
        return {}
    if not isinstance(extra, list) or len(extra) >= MAX_SEGMENTS:
        raise Invalid(CONF_SEGMENTS, "invalid_value")
    segments = []
    for index, segment in enumerate(extra):
        field = f"{CONF_SEGMENTS}.{index}"
        if not isinstance(segment, Mapping):
            raise Invalid(field, "invalid_value")
        try:
            segments.append(_segment(segment))
        except Invalid as err:
            raise Invalid(f"{field}.{err.field}", err.key, err.detail) from err
    # Spaces count here: the separator is kept as typed.
    separator = data.get(CONF_SEPARATOR)
    if separator is None:
        separator = DEFAULT_SEPARATOR
    if not isinstance(separator, str) or len(separator) > MAX_SEPARATOR:
        raise Invalid(CONF_SEPARATOR, "invalid_value")
    return {CONF_SEGMENTS: segments, CONF_SEPARATOR: separator}


def _layout(data: Mapping[str, Any], firmware: str, segments: int) -> dict[str, Any]:
    """The layout of a simple app on a TC002, if it has one.

    Two lines show exactly two entities, the other layouts one (the value
    and time layout shows the time next to its one entity).
    """
    layout = data.get(CONF_LAYOUT)
    if layout in (None, ""):
        return {}
    if layout not in LAYOUTS:
        raise Invalid(CONF_LAYOUT, "invalid_value")
    if firmware != FIRMWARE_AWTRIX_NG:
        raise Invalid(CONF_LAYOUT, "layout_ng_only")
    if layout == LAYOUT_TWO_LINES and segments != 1:
        raise Invalid(CONF_SEGMENTS, "layout_two_entities")
    if layout != LAYOUT_TWO_LINES and segments:
        raise Invalid(CONF_SEGMENTS, "layout_one_entity")
    clean = {CONF_LAYOUT: layout}
    # Only legible fonts are offered; the default is not stored.
    for key, fonts in ((CONF_VALUE_FONT, VALUE_FONTS), (CONF_TEXT_FONT, TEXT_FONTS)):
        font = data.get(key)
        if font in (None, "", fonts[0]):
            continue
        if font not in fonts:
            raise Invalid(key, "invalid_value")
        clean[key] = font
    return clean


def _display(data: Mapping[str, Any], firmware: str) -> dict[str, Any]:
    """The display options of an app (stored only when some are set)."""
    try:
        display = normalize_display(data.get(CONF_DISPLAY), firmware)
    except InvalidDisplayError as err:
        raise Invalid(f"{CONF_DISPLAY}.{err.field}", err.key) from err
    return {CONF_DISPLAY: display} if display else {}


# --------------------------------------------------------- whole items


def _siblings(
    entry: ConfigEntry, kind: str, subentry_id: str | None
) -> list[Mapping[str, Any]]:
    return [
        subentry.data
        for sid, subentry in entry.subentries.items()
        if subentry.subentry_type == kind and sid != subentry_id
    ]


def normalize_app(
    hass: HomeAssistant,
    entry: ConfigEntry,
    data: Mapping[str, Any],
    subentry_id: str | None,
    *,
    preview: bool = False,
) -> dict[str, Any]:
    """Return clean app data, or raise Invalid.

    In preview mode the name is not required and the template is not rendered
    here (the preview renders it continuously instead).
    """
    mode = data.get(CONF_MODE)
    if mode not in (MODE_SIMPLE, MODE_TEMPLATE):
        raise Invalid(CONF_MODE, "invalid_value")

    if subentry_id is not None:
        name = entry.subentries[subentry_id].data[CONF_APP_NAME]
    elif preview:
        name = "preview"
    else:
        name = data.get(CONF_APP_NAME)
        if isinstance(name, str):
            name = name.strip()
        if not isinstance(name, str) or not re.fullmatch(APP_NAME_PATTERN, name):
            raise Invalid(CONF_APP_NAME, "invalid_app_name")
        if any(
            o.get(CONF_APP_NAME) == name
            for o in _siblings(entry, SUBENTRY_TYPE_APP, None)
        ):
            raise Invalid(CONF_APP_NAME, "app_name_exists")

    if mode == MODE_SIMPLE:
        clean: dict[str, Any] = {
            CONF_MODE: MODE_SIMPLE,
            CONF_APP_NAME: name,
            **_segment(data),
            CONF_ICON: _text(data, CONF_ICON),
        }
        clean.update(_segments(data))
        clean.update(_icon_rules(data))
        layout = _layout(
            data, entry.data[CONF_FIRMWARE], len(clean.get(CONF_SEGMENTS, []))
        )
        clean.update(layout)
        if layout.get(CONF_LAYOUT) == LAYOUT_VALUE_BAR:
            # The bar is what this layout is for.
            clean.update(_progress({**data, CONF_PROGRESS: True}))
        else:
            clean.update(_progress(data))
        clean.update(_display(data, entry.data[CONF_FIRMWARE]))
        return clean

    clean = {
        CONF_MODE: MODE_TEMPLATE,
        CONF_APP_NAME: name,
        CONF_TEMPLATE: _raw_template(data) if preview else _template(hass, data),
        CONF_PAGE_INTERVAL: _number(
            data, CONF_PAGE_INTERVAL, MIN_PAGE_INTERVAL, MAX_PAGE_INTERVAL
        )
        or DEFAULT_PAGE_INTERVAL,
        CONF_CYCLE_ONLY_ON_SCREEN: _bool(
            data, CONF_CYCLE_ONLY_ON_SCREEN, DEFAULT_CYCLE_ONLY_ON_SCREEN
        ),
        CONF_HIDE_WHEN_EMPTY: _bool(
            data, CONF_HIDE_WHEN_EMPTY, DEFAULT_HIDE_WHEN_EMPTY
        ),
        CONF_LIFETIME: int(
            _number(data, CONF_LIFETIME, 0, MAX_LIFETIME) or DEFAULT_LIFETIME
        ),
        **_display(data, entry.data[CONF_FIRMWARE]),
    }
    # A default icon for the pages that name none; only kept when chosen.
    if icon := _text(data, CONF_ICON):
        clean[CONF_ICON] = icon
    return clean


def normalize_indicator(
    hass: HomeAssistant,
    entry: ConfigEntry,
    data: Mapping[str, Any],
    subentry_id: str | None,
    *,
    preview: bool = False,
) -> dict[str, Any]:
    """Return clean indicator data, or raise Invalid."""
    mode = data.get(CONF_MODE)
    if mode not in (MODE_SIMPLE, MODE_TEMPLATE):
        raise Invalid(CONF_MODE, "invalid_value")

    if subentry_id is not None:
        number = entry.subentries[subentry_id].data[CONF_INDICATOR]
    elif preview:
        number = (
            data.get(CONF_INDICATOR) if data.get(CONF_INDICATOR) in INDICATORS else "1"
        )
    else:
        number = data.get(CONF_INDICATOR)
        if number not in INDICATORS:
            raise Invalid(CONF_INDICATOR, "invalid_value")
        if any(
            o.get(CONF_INDICATOR) == number
            for o in _siblings(entry, SUBENTRY_TYPE_INDICATOR, None)
        ):
            raise Invalid(CONF_INDICATOR, "indicator_in_use")

    if mode == MODE_SIMPLE:
        effect = data.get(CONF_EFFECT, EFFECT_NONE)
        if effect not in EFFECTS:
            raise Invalid(CONF_EFFECT, "invalid_value")
        clean = {
            CONF_MODE: MODE_SIMPLE,
            CONF_INDICATOR: number,
            CONF_ENTITY_ID: _entity(data),
            CONF_COLOR_MODE: indicator_color_mode(data),
            CONF_EFFECT: effect,
            CONF_EFFECT_INTERVAL: int(
                _number(data, CONF_EFFECT_INTERVAL, 100, 10000)
                or DEFAULT_EFFECT_INTERVAL
            ),
        }
        if clean[CONF_COLOR_MODE] == COLOR_STATE:
            clean[CONF_ACTIVE_STATE] = (
                _text(data, CONF_ACTIVE_STATE, DEFAULT_ACTIVE_STATE)
                or DEFAULT_ACTIVE_STATE
            )
            clean[CONF_COLOR] = _color(data, CONF_COLOR, list(DEFAULT_INDICATOR_COLOR))
        else:
            clean.update(_gradient(data))
        return clean
    return {
        CONF_MODE: MODE_TEMPLATE,
        CONF_INDICATOR: number,
        CONF_TEMPLATE: _raw_template(data) if preview else _template(hass, data),
    }


def normalize(
    hass: HomeAssistant,
    entry: ConfigEntry,
    kind: str,
    data: Mapping[str, Any],
    subentry_id: str | None,
    *,
    preview: bool = False,
) -> dict[str, Any]:
    """Normalize app or indicator data."""
    if kind == SUBENTRY_TYPE_INDICATOR:
        return normalize_indicator(hass, entry, data, subentry_id, preview=preview)
    return normalize_app(hass, entry, data, subentry_id, preview=preview)
