"""Firmware drivers.

A driver only knows how to *transport* apps for a given firmware: which topic
to publish to, how to remove an app, how to tell which app is on screen and how
to detect a reboot. The app *content* is the firmware's native JSON, written by
the user in the template, and is never translated.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import ipaddress
import json
import re
from typing import Any, Final

from .const import FIRMWARE_AWTRIX3, FIRMWARE_AWTRIX_NG
from .loop import MAX_LOOP, parse_loop, valid_name

AWTRIX3_WIDTH: Final = 32
AWTRIX3_HEIGHT: Final = 8
# Names of effects, overlays and palettes accepted from a clock.
NAME_RE: Final = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
# Longest piece of an error message kept from a clock.
MAX_ERROR: Final = 120
# Largest screen accepted from a clock (a TC002 is 52 x 16).
MAX_SCREEN_PIXELS: Final = 64 * 32
# A firmware version: "1.2.2", maybe "v1.2.2" or "1.2.0-beta".
VERSION_RE: Final = re.compile(r"^v?(\d{1,4})\.(\d{1,4})(?:\.(\d{1,4}))?")
# AWTRIX NG takes inline icons as data URLs from 1.2.0 on, a bare Base64 JPEG
# before.
NG_DATA_URL_VERSION: Final = (1, 2, 0)


@dataclass(frozen=True, slots=True)
class Screen:
    """The clock's screen: colors (0xRRGGBB), row by row from the top left."""

    width: int
    height: int
    pixels: list[int]


@dataclass(frozen=True, slots=True)
class DisplayInfo:
    """How the clock draws apps."""

    width: int = AWTRIX3_WIDTH
    height: int = AWTRIX3_HEIGHT
    # Apps drawn at double size (TC002): each app pixel lights 2 x 2.
    scale: int = 1


@dataclass(frozen=True, slots=True)
class LoopState:
    """The app loop as read from the clock."""

    # Apps taking turns right now, in order.
    apps: list[str]
    # Apps switched off on the clock itself, kept off by a new order.
    disabled: list[str] = field(default_factory=list)


def _json(payload: str) -> Any:
    try:
        return json.loads(payload)
    except ValueError:
        return None


def _private_ipv4(value: Any) -> str | None:
    """Return a private IPv4 address read from the clock, else None."""
    if not isinstance(value, str):
        return None
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return None
    # The address is read from MQTT: never send requests outside the LAN.
    if address.version != 4 or not address.is_private or address.is_loopback:
        return None
    return str(address)


def _version(value: Any) -> tuple[int, int, int] | None:
    if not isinstance(value, str) or not (match := VERSION_RE.match(value.strip())):
        return None
    major, minor, patch = match.groups()
    return (int(major), int(minor), int(patch or 0))


def _file_names(entries: Any) -> set[str] | None:
    if not isinstance(entries, list):
        return None
    return {
        entry["name"]
        for entry in entries
        if isinstance(entry, dict) and isinstance(entry.get("name"), str)
    }


def _seconds(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _size(width: Any, height: Any) -> tuple[int, int] | None:
    """Return a plausible display size, else None."""
    if (
        not isinstance(width, int)
        or not isinstance(height, int)
        or isinstance(width, bool)
        or isinstance(height, bool)
        or width <= 0
        or height <= 0
        or width * height > MAX_SCREEN_PIXELS
    ):
        return None
    return width, height


def _screen(width: Any, height: Any, pixels: Any) -> Screen | None:
    if (
        _size(width, height) is None
        or not isinstance(pixels, list)
        or len(pixels) != width * height
    ):
        return None
    if not all(
        isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 0xFFFFFF
        for v in pixels
    ):
        return None
    return Screen(width, height, pixels)


class FirmwareDriver(ABC):
    """Transport details for one firmware family."""

    firmware: str
    # Largest payload the clock takes, in bytes (None: no known limit).
    max_payload: int | None = None
    # True when the clock stores the loop order itself and keeps apps in their
    # place: the order is then sent once, when the user changes it.
    keeps_order: bool = False

    def __init__(self, prefix: str) -> None:
        """Initialize the driver for a clock identified by its MQTT prefix."""
        self.prefix = prefix
        # The clock's firmware version, once it has told it.
        self.version: tuple[int, int, int] | None = None

    @abstractmethod
    def app_topic(self, app_name: str) -> str:
        """Return the topic used to publish an app."""

    @abstractmethod
    def indicator_topic(self, number: str) -> str:
        """Return the topic of a colored indicator (1, 2 or 3)."""

    @property
    @abstractmethod
    def remove_payload(self) -> str:
        """Return the payload that removes an app from the clock."""

    @property
    @abstractmethod
    def current_app_topic(self) -> str | None:
        """Return the topic announcing the app on screen, if any."""

    @property
    @abstractmethod
    def status_topic(self) -> str | None:
        """Return the topic carrying periodic device stats, if any."""

    @abstractmethod
    def parse_current_app(self, payload: str) -> str | None:
        """Extract the name of the app on screen from a message."""

    @abstractmethod
    def parse_uptime(self, payload: str) -> float | None:
        """Extract the device uptime in seconds from a status message."""

    @abstractmethod
    def with_lifetime(self, page: dict[str, Any], seconds: int) -> dict[str, Any]:
        """Return the page with the firmware's lifetime field set.

        A lifetime explicitly set by the user in the template is kept.
        """

    # Restart detection, besides the uptime: a topic saying whether the clock
    # is connected. Coming back online means it may have lost its apps.

    @property
    def online_topic(self) -> str | None:
        """Return the topic announcing that the clock is online, if any."""
        return None

    def parse_online(self, payload: str) -> bool | None:
        """Return True (online), False (offline) or None (unknown message)."""
        return None

    # Panel helpers: the screen mirror and navigation. A firmware that cannot
    # do something returns None and the panel hides the matching feature.

    @property
    def screen_topic(self) -> str | None:
        """Return the topic on which the clock sends its screen content."""
        return None

    def screen_request(self) -> tuple[str, str] | None:
        """Return the (topic, payload) asking the clock for its screen."""
        return None

    def parse_screen(self, payload: str) -> Screen | None:
        """Return the screen sent by the clock."""
        return None

    def show_app(self, app_name: str) -> tuple[str, str] | None:
        """Return the (topic, payload) bringing an app on screen now."""
        return None

    def navigate(self, forward: bool) -> tuple[str, str] | None:
        """Return the (topic, payload) moving to the next or previous app."""
        return None

    # Icons: files stored on the clock, and icons sent inline.

    def parse_address(self, payload: str) -> str | None:
        """Return the clock's IP address from a status message, if any."""
        return None

    def parse_version(self, payload: str) -> tuple[int, int, int] | None:
        """Return the firmware version from a status message, if any."""
        return None

    def icon_file(self, name: str) -> str | None:
        """Return the path of an icon file on the clock (None: not supported)."""
        return None

    @property
    def files_path(self) -> str | None:
        """Return the web path listing a folder of the clock (`?dir=`)."""
        return None

    def parse_files(self, payload: str) -> set[str] | None:
        """Return the file names of a folder listing, None if unreadable."""
        return None

    @property
    def upload_path(self) -> str | None:
        """Return the web path taking a file upload, named by its full path."""
        return None

    @abstractmethod
    def inline_icon(self, jpeg: str, gif: str | None) -> str:
        """Return the `icon` value carrying an image (Base64 JPEG, maybe a GIF)."""

    # App loop order. The loop is read over HTTP on the clock's web server;
    # the new order goes either over HTTP or over MQTT.

    @property
    def loop_path(self) -> str | None:
        """Return the HTTP path listing the apps of the loop, in order."""
        return None

    def parse_loop(self, payload: str) -> LoopState | None:
        """Return the loop described by the clock's answer."""
        return None

    @property
    def reorder_path(self) -> str | None:
        """Return the HTTP path taking the loop's new order (a list of names)."""
        return None

    def order_message(
        self, order: list[str], disabled: list[str]
    ) -> tuple[str, str] | None:
        """Return the (topic, payload) setting the loop order over MQTT."""
        return None

    # What the clock offers, and what it says about the commands it refuses.

    @property
    def capabilities_topic(self) -> str | None:
        """Return the topic listing the effects, overlays and palettes, if any."""
        return None

    def parse_capabilities(self, payload: str) -> dict[str, list[str]] | None:
        """Return the names the clock offers, by kind."""
        return None

    def parse_display_size(self, payload: str) -> tuple[int, int] | None:
        """Return the display's (width, height) from the capabilities message."""
        return None

    @property
    def settings_topic(self) -> str | None:
        """Return the topic carrying the clock's settings, if any."""
        return None

    def parse_enlarge(self, payload: str) -> bool | None:
        """Return whether apps are drawn at double size, from the settings."""
        return None

    @property
    def result_topics(self) -> list[str]:
        """Return the topic filters carrying the clock's answers to commands."""
        return []

    def parse_result(self, topic: str, payload: str) -> tuple[str, str | None] | None:
        """Return (command topic, refusal or None when accepted) for an answer."""
        return None

    @property
    def can_reorder(self) -> bool:
        """Return True if the loop order can be read and changed."""
        return self.loop_path is not None and (
            self.reorder_path is not None or self.order_message([], []) is not None
        )


class Awtrix3Driver(FirmwareDriver):
    """AWTRIX 3 (https://blueforcer.github.io/awtrix3/)."""

    firmware = FIRMWARE_AWTRIX3

    def app_topic(self, app_name: str) -> str:
        """Return the custom app topic."""
        return f"{self.prefix}/custom/{app_name}"

    def indicator_topic(self, number: str) -> str:
        """Indicators have one topic each."""
        return f"{self.prefix}/indicator{number}"

    @property
    def remove_payload(self) -> str:
        """An empty payload deletes a custom app or turns an indicator off."""
        return ""

    @property
    def current_app_topic(self) -> str:
        """AWTRIX 3 publishes the name of the app on screen."""
        return f"{self.prefix}/stats/currentApp"

    @property
    def status_topic(self) -> str:
        """AWTRIX 3 publishes its stats periodically."""
        return f"{self.prefix}/stats"

    def parse_current_app(self, payload: str) -> str | None:
        """The payload is the bare app name."""
        name = payload.strip()
        return name or None

    def parse_uptime(self, payload: str) -> float | None:
        """Read the `uptime` field of the stats JSON."""
        data = _json(payload)
        return _seconds(data.get("uptime")) if isinstance(data, dict) else None

    def with_lifetime(self, page: dict[str, Any], seconds: int) -> dict[str, Any]:
        """AWTRIX 3 uses `lifetime`, in seconds."""
        if "lifetime" in page:
            return page
        return {**page, "lifetime": seconds}

    @property
    def screen_topic(self) -> str:
        """The clock answers screen requests on this topic."""
        return f"{self.prefix}/screen"

    def screen_request(self) -> tuple[str, str]:
        """Any message on `sendscreen` asks for the screen."""
        return f"{self.prefix}/sendscreen", ""

    def parse_screen(self, payload: str) -> Screen | None:
        """The screen is a JSON array of 256 RGB integers."""
        return _screen(AWTRIX3_WIDTH, AWTRIX3_HEIGHT, _json(payload))

    def parse_address(self, payload: str) -> str | None:
        """The stats JSON carries `ip_address`; only private addresses count."""
        data = _json(payload)
        return _private_ipv4(data.get("ip_address")) if isinstance(data, dict) else None

    def icon_file(self, name: str) -> str:
        """Icons live in /ICONS; the app names them without the extension."""
        return f"/ICONS/{name}"

    @property
    def files_path(self) -> str:
        """The web page's file manager lists a folder on `/list`."""
        return "/list"

    def parse_files(self, payload: str) -> set[str] | None:
        """`[{"type": "file", "name": ...}, ...]`."""
        return _file_names(_json(payload))

    @property
    def upload_path(self) -> str:
        """The web page's file manager takes uploads on `/edit`."""
        return "/edit"

    def inline_icon(self, jpeg: str, gif: str | None) -> str:
        """AWTRIX 3 takes a bare Base64 JPEG and cannot play an inline GIF."""
        return jpeg

    def show_app(self, app_name: str) -> tuple[str, str]:
        """`switch` brings an app on screen by name."""
        return f"{self.prefix}/switch", json.dumps({"name": app_name})

    def navigate(self, forward: bool) -> tuple[str, str]:
        """`nextapp` and `previousapp` move through the rotation."""
        return f"{self.prefix}/{'nextapp' if forward else 'previousapp'}", ""

    @property
    def loop_path(self) -> str:
        """An object mapping each app name to its position."""
        return "/api/loop"

    def parse_loop(self, payload: str) -> LoopState | None:
        """AWTRIX 3 has no switched-off apps: they leave the loop instead."""
        apps = parse_loop(payload)
        return LoopState(apps) if apps is not None else None

    @property
    def reorder_path(self) -> str:
        """Only available over HTTP; apps left out leave the loop."""
        return "/api/reorder"


class AwtrixNGDriver(FirmwareDriver):
    """AWTRIX NG (https://blueforcer.github.io/awtrix-ng/), also on the TC002.

    Every command is published under `<prefix>/cmd/`, and the clock publishes
    its state under `<prefix>/state/` (retained).
    """

    firmware = FIRMWARE_AWTRIX_NG
    # Commands larger than this are dropped by the clock without an answer.
    max_payload = 8192
    keeps_order = True

    def app_topic(self, app_name: str) -> str:
        """Pushed apps have one topic each."""
        return f"{self.prefix}/cmd/apps/pushed/{app_name}"

    def indicator_topic(self, number: str) -> str:
        """Indicators have one topic each."""
        return f"{self.prefix}/cmd/indicators/{number}"

    @property
    def remove_payload(self) -> str:
        """An empty message deletes a pushed app or turns an indicator off."""
        return ""

    @property
    def current_app_topic(self) -> str:
        """The name of the app on screen, as a plain string (retained)."""
        return f"{self.prefix}/state/apps/active"

    @property
    def status_topic(self) -> str:
        """The device state, every 10 seconds by default (retained)."""
        return f"{self.prefix}/state/device"

    def parse_current_app(self, payload: str) -> str | None:
        """The payload is the bare app name."""
        name = payload.strip()
        return name or None

    def parse_uptime(self, payload: str) -> float | None:
        """Read `uptimeSeconds`.

        On a TC002 it counts from power-on and keeps counting when AWTRIX
        alone restarts: the online topic covers that case.
        """
        data = _json(payload)
        return _seconds(data.get("uptimeSeconds")) if isinstance(data, dict) else None

    def with_lifetime(self, page: dict[str, Any], seconds: int) -> dict[str, Any]:
        """AWTRIX NG uses `lifetimeMs`, in milliseconds."""
        if "lifetimeMs" in page:
            return page
        return {**page, "lifetimeMs": seconds * 1000}

    @property
    def online_topic(self) -> str:
        """`online` when the clock connects, `offline` (last will) when it drops."""
        return f"{self.prefix}/availability"

    def parse_online(self, payload: str) -> bool | None:
        """Read the availability message."""
        value = payload.strip().lower()
        if value == "online":
            return True
        if value == "offline":
            return False
        return None

    @property
    def screen_topic(self) -> str:
        """The clock answers screen requests on this topic."""
        return f"{self.prefix}/state/screen"

    def screen_request(self) -> tuple[str, str]:
        """`cmd/screen/get` asks for one screen message."""
        return f"{self.prefix}/cmd/screen/get", ""

    def parse_screen(self, payload: str) -> Screen | None:
        """`{"width": W, "height": H, "pixels": [...]}`, of any size."""
        data = _json(payload)
        if not isinstance(data, dict):
            return None
        return _screen(data.get("width"), data.get("height"), data.get("pixels"))

    def parse_address(self, payload: str) -> str | None:
        """The device state carries `ipAddress`; only private addresses count."""
        data = _json(payload)
        return _private_ipv4(data.get("ipAddress")) if isinstance(data, dict) else None

    def parse_version(self, payload: str) -> tuple[int, int, int] | None:
        """The device state carries `version`."""
        data = _json(payload)
        return _version(data.get("version")) if isinstance(data, dict) else None

    def icon_file(self, name: str) -> str:
        """Icons live in /ICONS; the app names them without the extension."""
        return f"/ICONS/{name}"

    @property
    def files_path(self) -> str:
        """`GET /api/v1/files?dir=` lists a folder."""
        return "/api/v1/files"

    def parse_files(self, payload: str) -> set[str] | None:
        """`{"files": [{"name": ..., "size": ...}], "usedBytes": ...}`."""
        data = _json(payload)
        return _file_names(data.get("files")) if isinstance(data, dict) else None

    @property
    def upload_path(self) -> str:
        """`POST /api/v1/files` stores a file named by its full path."""
        return "/api/v1/files"

    def inline_icon(self, jpeg: str, gif: str | None) -> str:
        """Images go in a data URL; an inline GIF plays, animated.

        Before 1.2.0, the clock took a bare Base64 JPEG, like AWTRIX 3, and
        read a data URL as a broken JPEG.
        """
        if self.version is not None and self.version < NG_DATA_URL_VERSION:
            return jpeg
        if gif is not None:
            return f"data:image/gif;base64,{gif}"
        return f"data:image/jpeg;base64,{jpeg}"

    def show_app(self, app_name: str) -> tuple[str, str]:
        """`apps/switch` takes the bare app name."""
        return f"{self.prefix}/cmd/apps/switch", app_name

    def navigate(self, forward: bool) -> tuple[str, str]:
        """`apps/next` and `apps/previous` move through the rotation."""
        return f"{self.prefix}/cmd/apps/{'next' if forward else 'previous'}", ""

    @property
    def loop_path(self) -> str:
        """Every app with its place in the loop; only readable over HTTP."""
        return "/api/v1/apps"

    def parse_loop(self, payload: str) -> LoopState | None:
        """Apps in the loop by `slot`, and the apps switched off."""
        data = _json(payload)
        if not isinstance(data, list) or len(data) > 4 * MAX_LOOP:
            return None
        slots: list[tuple[int, str]] = []
        disabled: list[str] = []
        for item in data:
            if not isinstance(item, dict) or not valid_name(name := item.get("name")):
                continue
            if item.get("enabled") is False and name not in disabled:
                disabled.append(name)
            slot = item.get("slot")
            if (
                item.get("inLoop") is True
                and item.get("present") is not False
                and isinstance(slot, int)
                and not isinstance(slot, bool)
            ):
                slots.append((slot, name))
        # An app shown twice per round has two slots: list it once.
        apps: list[str] = []
        for _slot, name in sorted(slots):
            if name not in apps:
                apps.append(name)
        return LoopState(apps, disabled)

    def order_message(self, order: list[str], disabled: list[str]) -> tuple[str, str]:
        """`apps/order` needs the complete list of switched-off apps."""
        return f"{self.prefix}/cmd/apps/order", json.dumps(
            {"order": order, "disabled": disabled}, ensure_ascii=False
        )

    @property
    def capabilities_topic(self) -> str:
        """Published once per connection (retained)."""
        return f"{self.prefix}/state/capabilities"

    def parse_capabilities(self, payload: str) -> dict[str, list[str]] | None:
        """Keep the effect, overlay and palette names, and only valid ones."""
        data = _json(payload)
        if not isinstance(data, dict):
            return None
        capabilities: dict[str, list[str]] = {}
        for kind in ("effects", "paletteEffects", "overlays", "palettes"):
            names = data.get(kind)
            if isinstance(names, list):
                capabilities[kind] = [
                    name
                    for name in names[:MAX_LOOP]
                    if isinstance(name, str) and NAME_RE.fullmatch(name)
                ]
        return capabilities

    def parse_display_size(self, payload: str) -> tuple[int, int] | None:
        """`display: {"width": 52, "height": 16}` on a TC002."""
        data = _json(payload)
        display = data.get("display") if isinstance(data, dict) else None
        if not isinstance(display, dict):
            return None
        return _size(display.get("width"), display.get("height"))

    @property
    def settings_topic(self) -> str:
        """The settings, on every change (retained)."""
        return f"{self.prefix}/state/settings"

    def parse_enlarge(self, payload: str) -> bool | None:
        """`enlargeApps` (TC002, on by default)."""
        data = _json(payload)
        value = data.get("enlargeApps") if isinstance(data, dict) else None
        return value if isinstance(value, bool) else None

    @property
    def result_topics(self) -> list[str]:
        """Every command is answered on its topic with `/result` added."""
        return [
            f"{self.prefix}/cmd/apps/pushed/+/result",
            f"{self.prefix}/cmd/indicators/+/result",
        ]

    def parse_result(self, topic: str, payload: str) -> tuple[str, str | None] | None:
        """`{"ok": true}`, or `{"ok": false, "error": {...}}` naming the field."""
        if not topic.endswith("/result"):
            return None
        data = _json(payload)
        if not isinstance(data, dict) or not isinstance(data.get("ok"), bool):
            return None
        command = topic.removesuffix("/result")
        if data["ok"]:
            return command, None
        error = data.get("error") if isinstance(data.get("error"), dict) else {}
        parts = [
            str(error[key])[:MAX_ERROR]
            for key in ("field", "message", "code")
            if isinstance(error.get(key), str) and error[key]
        ]
        return command, ": ".join(parts[:2]) or "refused"


DRIVERS: dict[str, type[FirmwareDriver]] = {
    FIRMWARE_AWTRIX3: Awtrix3Driver,
    FIRMWARE_AWTRIX_NG: AwtrixNGDriver,
}


def get_driver(firmware: str, prefix: str) -> FirmwareDriver:
    """Return the driver for a firmware."""
    return DRIVERS[firmware](prefix)
