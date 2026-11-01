"""Tests for firmware drivers."""

from __future__ import annotations

import json

import pytest

from custom_components.awtrix_studio.drivers import (
    Awtrix3Driver,
    AwtrixNGDriver,
    get_driver,
)


def test_awtrix3_topics() -> None:
    """AWTRIX 3 topics follow the documented layout."""
    driver = get_driver("awtrix3", "awtrix_1f1b94")
    assert isinstance(driver, Awtrix3Driver)
    assert driver.app_topic("tram") == "awtrix_1f1b94/custom/tram"
    assert driver.current_app_topic == "awtrix_1f1b94/stats/currentApp"
    assert driver.status_topic == "awtrix_1f1b94/stats"
    assert driver.remove_payload == ""


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ('{"uptime": 120, "bat": 99}', 120.0),
        ('{"uptime": 1.5}', 1.5),
        ('{"bat": 99}', None),
        ('{"uptime": "12"}', None),
        ('{"uptime": true}', None),
        ("not json", None),
        ("[1, 2]", None),
    ],
)
def test_awtrix3_uptime(payload: str, expected: float | None) -> None:
    """Uptime is read from the stats JSON when present."""
    assert Awtrix3Driver("awtrix").parse_uptime(payload) == expected


def test_awtrix3_current_app() -> None:
    """The current app payload is the bare name."""
    driver = Awtrix3Driver("awtrix")
    assert driver.parse_current_app(" tram\n") == "tram"
    assert driver.parse_current_app("") is None


def test_awtrix3_lifetime_keeps_user_value() -> None:
    """A lifetime set in the template wins."""
    driver = Awtrix3Driver("awtrix")
    assert driver.with_lifetime({"text": "a"}, 60) == {"text": "a", "lifetime": 60}
    assert driver.with_lifetime({"lifetime": 5}, 60) == {"lifetime": 5}


def test_parse_address() -> None:
    """Only private IPv4 addresses from the stats are used."""
    driver = Awtrix3Driver("awtrix")
    assert driver.parse_address('{"ip_address": "192.168.1.161"}') == "192.168.1.161"
    for payload in (
        '{"ip_address": "8.8.8.8"}',
        '{"ip_address": "127.0.0.1"}',
        '{"ip_address": "192.168.1.1/../x"}',
        '{"ip_address": 3}',
        "{}",
        "nope",
    ):
        assert driver.parse_address(payload) is None
    assert driver.icon_file("57980.gif") == "/ICONS/57980.gif"
    assert driver.files_path == "/list"
    assert driver.upload_path == "/edit"
    assert driver.parse_version('{"version": "0.98"}') is None


def test_file_lists() -> None:
    """Each firmware lists a folder in its own JSON."""
    assert Awtrix3Driver("a").parse_files(
        '[{"type": "file", "name": "co2.gif"}, {"type": "dir"}, 3]'
    ) == {"co2.gif"}
    ng = AwtrixNGDriver("n")
    listing = {
        "files": [{"name": "co2.gif", "size": 546}, {"size": 3}, "x"],
        "usedBytes": 65819,
        "totalBytes": 6955291,
    }
    assert ng.parse_files(json.dumps(listing)) == {"co2.gif"}
    for bad in ("[]", '{"files": 3}', "nope"):
        assert ng.parse_files(bad) is None


# ----------------------------------------------------------------- AWTRIX NG


def test_ng_topics() -> None:
    """AWTRIX NG commands live under cmd/, its state under state/."""
    driver = get_driver("awtrix_ng", "awtrixNG")
    assert isinstance(driver, AwtrixNGDriver)
    assert driver.app_topic("tram") == "awtrixNG/cmd/apps/pushed/tram"
    assert driver.indicator_topic("2") == "awtrixNG/cmd/indicators/2"
    assert driver.current_app_topic == "awtrixNG/state/apps/active"
    assert driver.status_topic == "awtrixNG/state/device"
    assert driver.online_topic == "awtrixNG/availability"
    assert driver.remove_payload == ""
    assert driver.show_app("tram") == ("awtrixNG/cmd/apps/switch", "tram")
    assert driver.navigate(True) == ("awtrixNG/cmd/apps/next", "")
    assert driver.navigate(False) == ("awtrixNG/cmd/apps/previous", "")
    assert driver.screen_request() == ("awtrixNG/cmd/screen/get", "")
    assert driver.screen_topic == "awtrixNG/state/screen"
    assert driver.icon_file("x.gif") == "/ICONS/x.gif"
    assert driver.files_path == "/api/v1/files"
    assert driver.upload_path == "/api/v1/files"
    assert driver.can_reorder
    assert driver.keeps_order


def test_ng_state() -> None:
    """Uptime, address and availability are read from the state topics."""
    driver = AwtrixNGDriver("awtrixNG")
    state = '{"uptimeSeconds": 4213, "ipAddress": "192.168.1.42"}'
    assert driver.parse_uptime(state) == 4213.0
    assert driver.parse_address(state) == "192.168.1.42"
    assert driver.parse_address('{"ipAddress": "8.8.8.8"}') is None
    assert driver.parse_uptime('{"uptime": 3}') is None
    assert driver.parse_online("online") is True
    assert driver.parse_online("offline\n") is False
    assert driver.parse_online("maybe") is None
    assert driver.parse_current_app(" weather ") == "weather"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1.2.2", (1, 2, 2)),
        ("v1.1", (1, 1, 0)),
        ("1.3.0-beta", (1, 3, 0)),
        ("abc", None),
        (12, None),
    ],
)
def test_ng_version(value: object, expected: tuple[int, int, int] | None) -> None:
    """The device state carries the firmware version."""
    driver = AwtrixNGDriver("awtrixNG")
    assert driver.parse_version(json.dumps({"version": value})) == expected
    assert driver.parse_version("nope") is None


def test_ng_lifetime_is_in_milliseconds() -> None:
    """`lifetimeMs` is added unless the template set it."""
    driver = AwtrixNGDriver("awtrixNG")
    assert driver.with_lifetime({"text": "a"}, 120) == {
        "text": "a",
        "lifetimeMs": 120000,
    }
    assert driver.with_lifetime({"lifetimeMs": 5}, 120) == {"lifetimeMs": 5}


def test_ng_screen_of_any_size() -> None:
    """A TC002 sends 52 x 16 pixels; the size comes with the screen."""
    driver = AwtrixNGDriver("awtrixNG")
    screen = driver.parse_screen(
        json.dumps({"width": 52, "height": 16, "pixels": [0xFF0000] * 832})
    )
    assert screen is not None
    assert (screen.width, screen.height, len(screen.pixels)) == (52, 16, 832)
    for bad in (
        {"width": 52, "height": 16, "pixels": [0] * 831},
        {"width": 4096, "height": 4096, "pixels": []},
        {"width": True, "height": 1, "pixels": [0]},
        {"width": 1, "height": 1, "pixels": ["#FF0000"]},
        [0] * 256,
    ):
        assert driver.parse_screen(json.dumps(bad)) is None


def test_ng_loop_and_order() -> None:
    """The loop comes by slot; the order keeps the apps switched off."""
    driver = AwtrixNGDriver("awtrixNG")
    apps = [
        {"name": "Time", "enabled": True, "inLoop": True, "slot": 0, "present": True},
        {"name": "weather", "enabled": True, "inLoop": True, "slot": 2},
        {"name": "Time", "enabled": True, "inLoop": True, "slot": 3},
        {"name": "tram", "enabled": True, "inLoop": True, "slot": 1},
        {"name": "co2", "enabled": True, "inLoop": False, "slot": 4, "present": False},
        {"name": "Date", "enabled": False, "inLoop": False, "slot": None},
        {"name": "", "enabled": False},
        "junk",
    ]
    state = driver.parse_loop(json.dumps(apps))
    assert state is not None
    assert state.apps == ["Time", "tram", "weather"]
    assert state.disabled == ["Date"]
    assert driver.parse_loop('{"Time": 0}') is None

    topic, payload = driver.order_message(["tram", "Time"], ["Date"])
    assert topic == "awtrixNG/cmd/apps/order"
    assert json.loads(payload) == {"order": ["tram", "Time"], "disabled": ["Date"]}


def test_inline_icons() -> None:
    """AWTRIX 3 takes a bare JPEG; AWTRIX NG a data URL, GIF when there is one."""
    assert Awtrix3Driver("a").inline_icon("JPEG", "GIF") == "JPEG"
    ng = AwtrixNGDriver("a")
    assert ng.inline_icon("JPEG", None) == "data:image/jpeg;base64,JPEG"
    assert ng.inline_icon("JPEG", "GIF") == "data:image/gif;base64,GIF"
    # Before 1.2.0, AWTRIX NG took a bare Base64 JPEG.
    ng.version = (1, 1, 2)
    assert ng.inline_icon("JPEG", "GIF") == "JPEG"
    ng.version = (1, 2, 0)
    assert ng.inline_icon("JPEG", None) == "data:image/jpeg;base64,JPEG"


def test_ng_capabilities_and_results() -> None:
    """The clock's names are kept when valid; refusals say what is wrong."""
    driver = AwtrixNGDriver("awtrixNG")
    capabilities = driver.parse_capabilities(
        json.dumps(
            {
                "effects": ["Plasma", "<b>", 3],
                "overlays": ["snow"],
                "palettes": ["Lava"],
                "transitions": ["Slide"],
            }
        )
    )
    assert capabilities == {
        "effects": ["Plasma"],
        "overlays": ["snow"],
        "palettes": ["Lava"],
    }
    assert driver.parse_capabilities("[]") is None

    topic = "awtrixNG/cmd/apps/pushed/tram/result"
    assert driver.parse_result(topic, '{"ok":true}') == (
        "awtrixNG/cmd/apps/pushed/tram",
        None,
    )
    refused = json.dumps(
        {
            "ok": False,
            "error": {
                "code": "validationFailed",
                "message": "unknown key",
                "field": "color",
            },
        }
    )
    assert driver.parse_result(topic, refused) == (
        "awtrixNG/cmd/apps/pushed/tram",
        "color: unknown key",
    )
    assert driver.parse_result(topic, '{"ok":false}') == (
        "awtrixNG/cmd/apps/pushed/tram",
        "refused",
    )
    assert driver.parse_result(topic, "nope") is None
    assert driver.parse_result("awtrixNG/cmd/apps/pushed/tram", "{}") is None


def test_ng_display_size_and_enlarge() -> None:
    """The display size comes with the capabilities, double size with the settings."""
    driver = AwtrixNGDriver("awtrixNG")
    assert driver.parse_display_size(
        json.dumps({"display": {"width": 52, "height": 16, "configurable": False}})
    ) == (52, 16)
    for bad in ({"display": {"width": 0, "height": 8}}, {"display": "52x16"}, []):
        assert driver.parse_display_size(json.dumps(bad)) is None
    assert driver.settings_topic == "awtrixNG/state/settings"
    assert driver.parse_enlarge('{"enlargeApps": false}') is False
    assert driver.parse_enlarge('{"brightness": 3}') is None
