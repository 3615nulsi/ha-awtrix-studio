"""Display options: AWTRIX custom app keys set from a form.

Apps in both modes can carry display options (how long the app stays, an
effect behind the text, scrolling, text style…). They are applied to every
page as defaults: a key the template sets itself always wins.

Options are stored with the names of AWTRIX 3 keys, checked and bounded
here for the clock's firmware; only options that differ from the firmware's
default are stored. For AWTRIX NG they are written with NG's own keys when the
app is published (`native_display`): milliseconds, words instead of numbers,
one `scroll` object and one app palette shared by the text and the effect.

Kept free of Home Assistant imports so it can be tested in isolation.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import math
import re
from typing import Any, Final

from .const import FIRMWARE_AWTRIX3, FIRMWARE_AWTRIX_NG

# Key of the display options in an app's stored data.
CONF_DISPLAY: Final = "display"

EFFECTS: Final = (
    "BrickBreaker",
    "Checkerboard",
    "ColorWaves",
    "Fade",
    "Fireworks",
    "LookingEyes",
    "Matrix",
    "MovingLine",
    "Pacifica",
    "PingPong",
    "Plasma",
    "PlasmaCloud",
    "Radar",
    "Ripple",
    "Snake",
    "SwirlIn",
    "SwirlOut",
    "TheaterChase",
    "TwinklingStars",
)
PALETTES: Final = (
    "Cloud",
    "Lava",
    "Ocean",
    "Forest",
    "Stripe",
    "Party",
    "Heat",
    "Rainbow",
)
OVERLAYS: Final = ("clear", "snow", "rain", "drizzle", "storm", "thunder", "frost")
# AWTRIX NG has no "clear": an app without an overlay shows the global one.
NG_OVERLAYS: Final = ("snow", "rain", "drizzle", "storm", "thunder", "frost")
# Options AWTRIX NG has no key for: dropped on NG clocks.
NG_UNSUPPORTED: Final = ("topText", "save")

# Effects and palettes added to the firmware later, or palette files stored on
# the clock, are accepted by name.
NAME_RE: Final = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
HEX_COLOR_RE: Final = re.compile(r"^#[0-9A-Fa-f]{6}$")


class InvalidDisplayError(ValueError):
    """A display option that cannot be accepted."""

    def __init__(self, field: str, key: str) -> None:
        """Remember which option failed and why."""
        super().__init__(f"{field}: {key}")
        self.field = field
        self.key = key


def _int(data: Mapping[str, Any], key: str, low: int, high: int) -> int | None:
    value = data.get(key)
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise InvalidDisplayError(key, "invalid_value")
    try:
        number = float(value)
    except (TypeError, ValueError) as err:
        raise InvalidDisplayError(key, "invalid_value") from err
    if not math.isfinite(number):
        raise InvalidDisplayError(key, "invalid_value")
    return int(max(low, min(high, round(number))))


def _float(data: Mapping[str, Any], key: str, low: float, high: float) -> float | None:
    value = data.get(key)
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise InvalidDisplayError(key, "invalid_value")
    try:
        number = float(value)
    except (TypeError, ValueError) as err:
        raise InvalidDisplayError(key, "invalid_value") from err
    if not math.isfinite(number):
        raise InvalidDisplayError(key, "invalid_value")
    return round(max(low, min(high, number)), 1)


def _bool(data: Mapping[str, Any], key: str) -> bool | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, bool):
        raise InvalidDisplayError(key, "invalid_value")
    return value


def _choice(data: Mapping[str, Any], key: str, choices: tuple[int, ...]) -> int | None:
    value = data.get(key)
    if value is None or value == "":
        return None
    if isinstance(value, bool) or value not in choices:
        raise InvalidDisplayError(key, "invalid_value")
    return int(value)


def _color(data: Mapping[str, Any], key: str) -> str | None:
    value = data.get(key)
    if value is None or value == "":
        return None
    if isinstance(value, str) and HEX_COLOR_RE.fullmatch(value):
        return value.upper()
    if (
        isinstance(value, (list, tuple))
        and len(value) == 3
        and all(isinstance(c, int) and not isinstance(c, bool) for c in value)
        and all(0 <= c <= 255 for c in value)
    ):
        return "#{:02X}{:02X}{:02X}".format(*value)
    raise InvalidDisplayError(key, "invalid_color")


def _name(data: Mapping[str, Any], key: str) -> str | None:
    value = data.get(key)
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not NAME_RE.fullmatch(value):
        raise InvalidDisplayError(key, "invalid_value")
    return value


def _effect_settings(data: Mapping[str, Any], firmware: str) -> dict[str, Any] | None:
    value = data.get("effectSettings")
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise InvalidDisplayError("effectSettings", "invalid_value")
    clean: dict[str, Any] = {}
    if firmware == FIRMWARE_AWTRIX_NG:
        # A multiplier of the effect's own pace, 1.0 by default.
        speed = _float(value, "speed", 0.1, 10.0)
        if speed is not None and speed != 1.0:
            clean["speed"] = speed
    elif (speed := _int(value, "speed", 1, 20)) is not None:
        clean["speed"] = speed
    if (palette := _name(value, "palette")) is not None:
        clean["palette"] = palette
    if (blend := _bool(value, "blend")) is not None:
        clean["blend"] = blend
    return clean or None


def normalize_display(data: Any, firmware: str = FIRMWARE_AWTRIX3) -> dict[str, Any]:
    """Return the display options worth storing, or raise InvalidDisplayError.

    Unknown keys are dropped, and so are options the firmware does not have.
    Values equal to the firmware's default are dropped too, so that an app
    only carries what was actually changed.
    """
    if data is None:
        return {}
    if not isinstance(data, Mapping):
        raise InvalidDisplayError(CONF_DISPLAY, "invalid_value")

    clean: dict[str, Any] = {}

    def keep(key: str, value: Any, default: Any = None) -> None:
        if value is not None and value != default:
            clean[key] = value

    # How long the app stays, and how many times its text scrolls.
    keep("duration", _int(data, "duration", 1, 3600))
    keep("repeat", _int(data, "repeat", -1, 100), -1)

    # Text placement and scrolling.
    keep("textCase", _choice(data, "textCase", (0, 1, 2)), 0)
    keep("topText", _bool(data, "topText"), False)
    keep("textOffset", _int(data, "textOffset", -32, 32), 0)
    keep("center", _bool(data, "center"), True)
    keep("noScroll", _bool(data, "noScroll"), False)
    keep("scrollSpeed", _int(data, "scrollSpeed", 10, 500), 100)
    keep("pushIcon", _choice(data, "pushIcon", (0, 1, 2)), 0)

    # Text style: these replace each other on the clock, so one at most.
    styles = {
        "rainbow": _bool(data, "rainbow") or None,
        "gradient": _gradient(data),
        "blinkText": _int(data, "blinkText", 50, 60000),
        "fadeText": _int(data, "fadeText", 50, 60000),
    }
    chosen = [key for key, value in styles.items() if value is not None]
    if len(chosen) > 1:
        raise InvalidDisplayError(chosen[1], "text_style_conflict")
    for key in chosen:
        clean[key] = styles[key]

    # Behind the text.
    keep("background", _color(data, "background"))
    effect = _name(data, "effect")
    if effect is not None:
        clean["effect"] = effect
        keep("effectSettings", _effect_settings(data, firmware))
    overlay = data.get("overlay")
    overlays = NG_OVERLAYS if firmware == FIRMWARE_AWTRIX_NG else OVERLAYS
    if overlay not in (None, ""):
        if overlay not in overlays:
            raise InvalidDisplayError("overlay", "invalid_value")
        clean["overlay"] = overlay

    # What the clock does with the app.
    keep("lifetimeMode", _choice(data, "lifetimeMode", (0, 1)), 0)
    keep("save", _bool(data, "save"), False)
    if firmware == FIRMWARE_AWTRIX_NG:
        for key in NG_UNSUPPORTED:
            clean.pop(key, None)
    return clean


def _gradient(data: Mapping[str, Any]) -> list[str] | None:
    value = data.get("gradient")
    if value is None or value == "":
        return None
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise InvalidDisplayError("gradient", "invalid_color")
    colors = [_color({"gradient": c}, "gradient") for c in value]
    if None in colors:
        raise InvalidDisplayError("gradient", "invalid_color")
    return colors


TEXT_CASES: Final = ("inherit", "upper", "asTyped")
ICON_MODES: Final = ("fixed", "pushOnce", "push")
LIFETIME_EXPIRY: Final = ("remove", "mark")


def _same(value: Any) -> Any:
    return value


# Options that only change name (and sometimes form) on AWTRIX NG.
NG_KEYS: Final[dict[str, tuple[str, Callable[[Any], Any]]]] = {
    "duration": ("durationMs", lambda seconds: seconds * 1000),
    "textCase": ("textCase", TEXT_CASES.__getitem__),
    "textOffset": ("textOffsetX", _same),
    "center": ("textAlign", lambda center: "center" if center else "start"),
    "pushIcon": ("iconMode", ICON_MODES.__getitem__),
    "blinkText": ("textBlinkMs", _same),
    "fadeText": ("textFadeMs", _same),
    "background": ("backgroundColor", _same),
    "lifetimeMode": ("lifetimeExpiry", LIFETIME_EXPIRY.__getitem__),
}


def ng_display(display: Mapping[str, Any]) -> dict[str, Any]:
    """Write stored display options with AWTRIX NG's keys."""
    out = {
        name: convert(display[key])
        for key, (name, convert) in NG_KEYS.items()
        if key in display
    }
    # AWTRIX 3 writes -1 for "no limit", AWTRIX NG 0 (its default).
    if display.get("repeat", -1) > 0:
        out["repeat"] = display["repeat"]
    scroll: dict[str, Any] = {}
    if display.get("noScroll"):
        scroll["mode"] = "static"
    if "scrollSpeed" in display:
        scroll["speed"] = display["scrollSpeed"]
    if scroll:
        out["scroll"] = scroll
    out.update(_ng_effect(display))
    if display.get("overlay") not in (None, "clear"):
        out["overlay"] = display["overlay"]
    return out


def _ng_effect(display: Mapping[str, Any]) -> dict[str, Any]:
    """The effect, and the one palette an NG app shares with its text."""
    out: dict[str, Any] = {}
    palette: str | list[str] | None = None
    if display.get("rainbow"):
        palette = "Rainbow"
    elif "gradient" in display:
        palette = list(display["gradient"])
    if "effect" in display:
        out["effect"] = display["effect"]
        settings = display.get("effectSettings") or {}
        if "speed" in settings:
            out["effectSpeed"] = settings["speed"]
        if "blend" in settings:
            out["paletteBlend"] = settings["blend"]
        # The text style's palette wins: the app has only one.
        if palette is None:
            palette = settings.get("palette")
    if palette is not None:
        out["palette"] = palette
    return out


def native_display(display: Mapping[str, Any], firmware: str) -> dict[str, Any]:
    """Return the display options written in the firmware's own keys."""
    if firmware == FIRMWARE_AWTRIX_NG:
        return ng_display(display)
    return dict(display)


# Next to a `layout`, AWTRIX NG only takes these keys; the others go into the
# layout (what is behind the boxes) or into its text regions (how text looks
# and moves). Placement keys have no meaning there: each box places its text.
LAYOUT_PAGE_KEYS: Final = ("durationMs", "repeat", "lifetimeMs", "lifetimeExpiry")
LAYOUT_KEYS: Final = (
    "effect",
    "effectSpeed",
    "overlay",
    "palette",
    "paletteBlend",
    "backgroundColor",
)
TEXT_REGION_KEYS: Final = ("textCase", "textBlinkMs", "textFadeMs", "scroll")


def _apply_to_layout(
    page: dict[str, Any], defaults: Mapping[str, Any], paint_text: bool
) -> dict[str, Any]:
    """Spread NG display options over a layout page, where NG takes them."""
    merged = {k: defaults[k] for k in LAYOUT_PAGE_KEYS if k in defaults}
    merged.update(page)
    layout = dict(page["layout"])
    for key in LAYOUT_KEYS:
        if key in defaults and key not in layout:
            layout[key] = defaults[key]
    # An effect is drawn instead of a background color, never with one.
    if "effect" in layout and "backgroundColor" not in page["layout"]:
        layout.pop("backgroundColor", None)
    regions = layout.get("regions")
    if isinstance(regions, list):
        painted = paint_text and "palette" not in page["layout"]
        layout["regions"] = [
            _text_region_defaults(region, defaults, painted) for region in regions
        ]
    merged["layout"] = layout
    return merged


def _text_region_defaults(
    region: Any, defaults: Mapping[str, Any], painted: bool
) -> Any:
    if not isinstance(region, dict) or "text" not in region:
        return region
    out = {k: defaults[k] for k in TEXT_REGION_KEYS if k in defaults}
    out.update(region)
    if painted and "palette" not in region:
        out.pop("textColor", None)
        out["color"] = "palette"
    return out


def apply_display(
    pages: list[dict[str, Any]],
    display: Mapping[str, Any] | None,
    firmware: str = FIRMWARE_AWTRIX3,
) -> list[dict[str, Any]]:
    """Return the pages with the display options added where not already set."""
    if not display:
        return pages
    defaults = native_display(display, firmware)
    # On NG a rainbow or gradient text needs `textColor: "palette"`, which
    # must win over the page's own text color, unless the page brings its
    # own palette.
    paint_text = firmware == FIRMWARE_AWTRIX_NG and (
        display.get("rainbow") or "gradient" in display
    )
    result = []
    for page in pages:
        if firmware == FIRMWARE_AWTRIX_NG and isinstance(page.get("layout"), dict):
            result.append(_apply_to_layout(page, defaults, bool(paint_text)))
            continue
        merged = {**defaults, **page}
        if paint_text and "palette" not in page:
            merged["textColor"] = "palette"
        result.append(merged)
    return result
