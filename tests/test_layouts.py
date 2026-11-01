"""Tests for simple-mode layouts: an app on the whole display of a TC002."""

from __future__ import annotations

import base64
from io import BytesIO
import json
from typing import Any

from homeassistant.core import HomeAssistant
from PIL import Image
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
from pytest_homeassistant_custom_component.typing import (
    MqttMockHAClient,
    WebSocketGenerator,
)

from custom_components.awtrix_studio.const import (
    DOMAIN,
    FIRMWARE_AWTRIX3,
    FIRMWARE_AWTRIX_NG,
)
from custom_components.awtrix_studio.display import apply_display
from custom_components.awtrix_studio.drivers import AwtrixNGDriver
from custom_components.awtrix_studio.icons import ICON_URL, IconStore, convert
from custom_components.awtrix_studio.templates import (
    InvalidSimpleOptionsError,
    build_app_template,
)

from .test_icons import png
from .test_ng import PREFIX, ng_entry, setup
from .test_templates import render

NG = FIRMWARE_AWTRIX_NG
ICON = {"id": "icon", "box": [0, 0, 16, 16], "icon": "2422"}


def regions(hass: HomeAssistant, options: dict[str, Any]) -> list[dict[str, Any]]:
    """Render a layout app and return the regions of its only page."""
    pages = render(hass, build_app_template(options, NG))
    assert len(pages) == 1
    assert set(pages[0]) == {"layout"}
    assert pages[0]["layout"]["version"] == 1
    return pages[0]["layout"]["regions"]


async def test_value(hass: HomeAssistant) -> None:
    """The value fills the space next to the icon, label and unit included."""
    hass.states.async_set("sensor.t", "21.46", {"unit_of_measurement": "°C"})
    options = {
        "layout": "value",
        "entity_id": "sensor.t",
        "label": "In",
        "icon": "2422",
        "decimals": 1,
        "color": [255, 150, 30],
    }
    assert regions(hass, options) == [
        ICON,
        {
            "id": "value",
            "box": [17, 0, 35, 16],
            "text": "In 21.5°C",
            "font": "matrix-chunky8",
            "color": "#FF961E",
        },
    ]


async def test_title_value(hass: HomeAssistant) -> None:
    """The label becomes a title above the value."""
    hass.states.async_set("sensor.h", "48", {"unit_of_measurement": "%"})
    options = {"layout": "title_value", "entity_id": "sensor.h", "label": "Salon"}
    assert regions(hass, options) == [
        {
            "id": "title",
            "box": [0, 0, 52, 7],
            "text": "Salon",
            "font": "matrix-light6",
            "color": "#969696",
        },
        {
            "id": "value",
            "box": [0, 8, 52, 8],
            "text": "48%",
            "font": "matrix-chunky8",
            "color": "#FFFFFF",
        },
    ]
    # Without a label, the value takes the whole height.
    options["label"] = ""
    assert regions(hass, options)[0]["box"] == [0, 0, 52, 16]


async def test_value_bar(hass: HomeAssistant) -> None:
    """A progress bar under the value, from another entity."""
    hass.states.async_set("sensor.p", "1.8", {"unit_of_measurement": "kW"})
    hass.states.async_set("sensor.load", "60")
    options = {
        "layout": "value_bar",
        "entity_id": "sensor.p",
        "icon": "2422",
        "progress_entity": "sensor.load",
        "progress_color": [40, 220, 80],
        "color_mode": "steps",
        "threshold_1": 1,
        "color_1": [255, 0, 0],
    }
    assert regions(hass, options)[1:] == [
        {
            "id": "value",
            "box": [17, 0, 35, 11],
            "text": "1.8 kW",
            "font": "matrix-chunky8",
            "color": "#FF0000",
        },
        {
            "id": "bar",
            "box": [17, 12, 35, 3],
            "progress": 60,
            "color": "#28DC50",
            "trackColor": "#2E2E2E",
        },
    ]
    # No bar while the source has no number.
    hass.states.async_set("sensor.load", "unknown")
    assert [r["id"] for r in regions(hass, options)] == ["icon", "value"]


async def test_two_lines(hass: HomeAssistant) -> None:
    """Two entities, one per line; one alone takes the whole height."""
    hass.states.async_set("sensor.in", "21.5", {"unit_of_measurement": "°"})
    hass.states.async_set("sensor.out", "12.4", {"unit_of_measurement": "°"})
    options = {
        "layout": "two_lines",
        "entity_id": "sensor.in",
        "label": "IN",
        "icon": "2422",
        "color": [255, 150, 30],
        "segments": [{"entity_id": "sensor.out", "label": "EX", "color": "#00AAFF"}],
    }
    assert regions(hass, options) == [
        ICON,
        {
            "id": "line1",
            "box": [17, 0, 35, 8],
            "text": "IN 21.5°",
            "font": "matrix-light6",
            "color": "#FF961E",
            "align": "start",
        },
        {
            "id": "line2",
            "box": [17, 8, 35, 8],
            "text": "EX 12.4°",
            "font": "matrix-light6",
            "color": "#00AAFF",
            "align": "start",
        },
    ]
    hass.states.async_set("sensor.in", "unavailable")
    assert regions(hass, options)[1] == {
        "id": "line1",
        "box": [17, 0, 35, 16],
        "text": "EX 12.4°",
        "font": "matrix-chunky8",
        "color": "#00AAFF",
    }
    hass.states.async_set("sensor.out", "unavailable")
    assert render(hass, build_app_template(options, NG)) == []


async def test_hidden_without_value(hass: HomeAssistant) -> None:
    """Like a line app, a layout app hides without a value, or at 0 if asked."""
    options = {"layout": "value", "entity_id": "sensor.x", "hide_when_zero": True}
    hass.states.async_set("sensor.x", "unknown")
    assert render(hass, build_app_template(options, NG)) == []
    hass.states.async_set("sensor.x", "0")
    assert render(hass, build_app_template(options, NG)) == []


async def test_text_is_quoted(hass: HomeAssistant) -> None:
    """A title or an icon name cannot end its string and run as code."""
    hass.states.async_set("sensor.x", "1")
    options = {
        "layout": "title_value",
        "entity_id": "sensor.x",
        "label": '"}{{ 7 * 7 }}',
        "icon": "a\"'{%",
    }
    found = regions(hass, options)
    assert found[0]["icon"] == "a\"'{%"
    assert found[1]["text"] == '"}{{ 7 * 7 }}'


async def test_layout_only_on_ng(hass: HomeAssistant) -> None:
    """AWTRIX 3 has no layouts: the app stays one line."""
    hass.states.async_set("sensor.x", "1")
    options = {"layout": "value", "entity_id": "sensor.x"}
    assert render(hass, build_app_template(options, FIRMWARE_AWTRIX3)) == [
        {"text": "1", "color": "#FFFFFF"}
    ]


def test_bad_progress_range() -> None:
    """The bar needs a maximum above its minimum."""
    with pytest.raises(InvalidSimpleOptionsError):
        build_app_template(
            {
                "layout": "value_bar",
                "entity_id": "sensor.x",
                "progress_min": 10,
                "progress_max": 10,
            },
            NG,
        )


def test_display_options_on_a_layout() -> None:
    """Display options go where AWTRIX NG takes them next to a layout."""
    page = {
        "layout": {
            "version": 1,
            "regions": [
                {"id": "icon", "box": [0, 0, 16, 16], "icon": "sun"},
                {
                    "id": "value",
                    "box": [17, 0, 35, 16],
                    "text": "1",
                    "color": "#FFFFFF",
                },
            ],
        }
    }
    display = {
        "duration": 8,
        "repeat": 2,
        "center": False,
        "textCase": 1,
        "noScroll": True,
        "rainbow": True,
        "effect": "Plasma",
        "background": "#102030",
        "pushIcon": 1,
    }
    [out] = apply_display([page], display, NG)
    assert out == {
        "durationMs": 8000,
        "repeat": 2,
        "layout": {
            "version": 1,
            "effect": "Plasma",
            "palette": "Rainbow",
            "regions": [
                {"id": "icon", "box": [0, 0, 16, 16], "icon": "sun"},
                {
                    "id": "value",
                    "box": [17, 0, 35, 16],
                    "text": "1",
                    "color": "palette",
                    "textCase": "upper",
                    "scroll": {"mode": "static"},
                },
            ],
        },
    }
    # The page is not changed in place, and what it sets itself wins.
    assert "durationMs" not in page
    own = {
        "durationMs": 1000,
        "layout": {"version": 1, "regions": [], "palette": "Ocean"},
    }
    [out] = apply_display([own], {"duration": 8, "background": "#102030"}, NG)
    assert out == {
        "durationMs": 1000,
        "layout": {
            "version": 1,
            "regions": [],
            "palette": "Ocean",
            "backgroundColor": "#102030",
        },
    }


async def test_icons_doubled_in_large_boxes(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A gallery icon in a 16x16 box is sent as a 16x16 GIF, animated if it is."""
    aioclient_mock.get(ICON_URL.format(id=9), content=png(frames=3))
    aioclient_mock.get(ICON_URL.format(id=10), content=png())
    aioclient_mock.get(ICON_URL.format(id=404), status=404)
    store = IconStore(hass)
    driver = AwtrixNGDriver("awtrixNG")

    def layout(*regions: dict[str, Any]) -> str:
        return json.dumps({"layout": {"version": 1, "regions": list(regions)}})

    def image(url: str) -> Image.Image:
        assert url.startswith("data:image/gif;base64,")
        return Image.open(BytesIO(base64.b64decode(url.split(",", 1)[1])))

    payload = layout(
        {"id": "a", "box": [0, 0, 16, 16], "icon": "lametric:9"},
        {"id": "b", "box": [17, 0, 16, 16], "icon": "10"},
        {"id": "c", "box": [40, 0, 8, 8], "icon": "10"},
        {"id": "d", "box": [40, 8, 8, 8], "icon": "sun"},
        {"id": "e", "box": [48, 0, 4, 4], "icon": "lametric:404"},
        {"id": "t", "box": [0, 0, 52, 16], "text": "x"},
    )
    page = json.loads(await store.async_resolve(payload, None, driver))
    regions = {r["id"]: r for r in page["layout"]["regions"]}
    assert list(regions) == ["a", "b", "c", "d", "t"]

    animated = image(regions["a"]["icon"])
    assert animated.size == (16, 16)
    assert animated.n_frames == 3
    still = image(regions["b"]["icon"]).convert("RGB")
    assert still.size == (16, 16)
    # The red pixel top left of the 8x8 icon now covers 2x2.
    assert {still.getpixel((x, y)) for x in (0, 1) for y in (0, 1)} == {(255, 0, 0)}
    assert still.getpixel((2, 2)) == (0, 0, 0)
    # A small box keeps the icon at its size; a clock file is left to the clock.
    assert regions["c"]["icon"] == f"data:image/jpeg;base64,{convert(png())['jpeg']}"
    assert regions["d"]["icon"] == "sun"

    # Too large for the clock with the animation: its first image.
    big = layout(
        {"id": "a", "box": [0, 0, 16, 16], "icon": "lametric:9"},
        {"id": "t", "box": [17, 0, 35, 16], "text": "x" * 7900},
    )
    page = json.loads(await store.async_resolve(big, None, driver))
    assert image(page["layout"]["regions"][0]["icon"]).n_frames == 1


@pytest.fixture
def expected_lingering_timers() -> bool:
    """The mocked MQTT client leaves its periodic housekeeping timer behind."""
    return True


async def test_save_and_publish_a_layout_app(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    mqtt_mock: MqttMockHAClient,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """The panel saves a layout app; the clock gets the layout, icon doubled."""
    aioclient_mock.get(ICON_URL.format(id=2422), content=png())
    hass.states.async_set("sensor.h", "48", {"unit_of_measurement": "%"})
    entry = ng_entry()
    await setup(hass, entry)
    client = await hass_ws_client(hass)

    async def send(kind: str, data: dict[str, Any]) -> dict[str, Any]:
        await client.send_json_auto_id(
            {
                "type": f"awtrix_studio/{kind}",
                "entry_id": entry.entry_id,
                "kind": "app",
                "data": data,
            }
        )
        response = await client.receive_json()
        assert response["success"], response
        return response

    app = {
        "mode": "simple",
        "app_name": "humidity",
        "entity_id": "sensor.h",
        "label": "Salon",
        "icon": "2422",
        "layout": "title_value",
        "segments": [],
    }
    await send("preview", app)
    event = (await client.receive_json())["event"]
    assert [r["id"] for r in event["pages"][0]["layout"]["regions"]] == [
        "icon",
        "title",
        "value",
    ]

    # Layouts show a fixed number of entities.
    two = {**app, "layout": "two_lines"}
    result = (await send("save", two))["result"]
    assert result["errors"] == {"segments": "layout_two_entities"}
    many = {**app, "segments": [{"entity_id": "sensor.h"}]}
    result = (await send("save", many))["result"]
    assert result["errors"] == {"segments": "layout_one_entity"}
    result = (await send("save", {**app, "layout": "grid"}))["result"]
    assert result["errors"] == {"layout": "invalid_value"}
    result = (await send("save", {**app, "value_font": "small"}))["result"]
    assert result["errors"] == {"value_font": "invalid_value"}

    app["value_font"] = "matrix-light8"
    app["text_font"] = "matrix-light6"
    subentry_id = (await send("save", app))["result"]["subentry_id"]
    await hass.async_block_till_done()
    stored = entry.subentries[subentry_id].data
    assert stored["layout"] == "title_value"
    # The default font is not stored.
    assert (stored["value_font"], "text_font" in stored) == ("matrix-light8", False)
    topic = f"{PREFIX}/cmd/apps/pushed/humidity"
    payloads = [
        c.args[1] for c in mqtt_mock.async_publish.call_args_list if c.args[0] == topic
    ]
    page = json.loads(payloads[-1])
    icon = page["layout"]["regions"][0]["icon"]
    assert icon.startswith("data:image/gif;base64,")
    assert Image.open(BytesIO(base64.b64decode(icon.split(",", 1)[1]))).size == (16, 16)
    assert page["layout"]["regions"][2]["text"] == "48%"


async def test_no_layout_on_awtrix3(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator, mqtt_mock: MqttMockHAClient
) -> None:
    """AWTRIX 3 has no layouts: saving one is refused."""
    hass.states.async_set("sensor.h", "48")
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="awtrix_3",
        unique_id="awtrix_3",
        data={"firmware": "awtrix3", "prefix": "awtrix_3"},
    )
    await setup(hass, entry)
    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {
            "type": "awtrix_studio/save",
            "entry_id": entry.entry_id,
            "kind": "app",
            "data": {
                "mode": "simple",
                "app_name": "h",
                "entity_id": "sensor.h",
                "layout": "value",
            },
        }
    )
    response = await client.receive_json()
    assert response["result"]["errors"] == {"layout": "layout_ng_only"}


async def test_fonts(hass: HomeAssistant) -> None:
    """The value and the title take the fonts chosen among legible ones."""
    hass.states.async_set("sensor.h", "48")
    options = {
        "layout": "title_value",
        "entity_id": "sensor.h",
        "label": "Salon",
        "value_font": "matrix-chunky8x6",
        "text_font": "matrix-chunky6",
    }
    title, value = regions(hass, options)
    assert (title["font"], value["font"]) == ("matrix-chunky6", "matrix-chunky8x6")
    # Two lines use the title font; one left alone takes the value font.
    hass.states.async_set("sensor.t", "21")
    options.update(layout="two_lines", segments=[{"entity_id": "sensor.t"}])
    assert [r["font"] for r in regions(hass, options)] == ["matrix-chunky6"] * 2
    hass.states.async_set("sensor.h", "unknown")
    assert regions(hass, options)[0]["font"] == "matrix-chunky8x6"
    # Anything else falls back to the defaults.
    options.update(layout="title_value", value_font="small", text_font="x")
    hass.states.async_set("sensor.h", "48")
    title, value = regions(hass, options)
    assert (title["font"], value["font"]) == ("matrix-light6", "matrix-chunky8")
