"""Tests for LaMetric icons served by Awtrix Studio, and progress bars."""

from __future__ import annotations

import base64
from io import BytesIO
import json
from typing import Any

from homeassistant.config_entries import ConfigSubentryDataWithId
from homeassistant.core import HomeAssistant
from homeassistant.helpers.template import Template
from PIL import Image
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_mqtt_message,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
from pytest_homeassistant_custom_component.typing import (
    MqttMockHAClient,
    WebSocketGenerator,
)

from custom_components.awtrix_studio.clock_files import ClockFiles, upload_form
from custom_components.awtrix_studio.const import DATA_ICONS, DOMAIN
from custom_components.awtrix_studio.drivers import Awtrix3Driver, AwtrixNGDriver
from custom_components.awtrix_studio.icons import (
    ICON_URL,
    SEARCH_URL,
    IconStore,
    convert,
    icon_id,
)
from custom_components.awtrix_studio.pages import parse_pages
from custom_components.awtrix_studio.templates import (
    InvalidSimpleOptionsError,
    build_app_template,
)

PREFIX = "awtrix_test"
RED = (255, 0, 0, 255)


def png(size: int = 8, *, frames: int = 1) -> bytes:
    """A small icon: a red pixel top left, transparent elsewhere."""
    images = []
    for frame in range(frames):
        image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        image.putpixel((0, 0), RED)
        image.putpixel((frame % size, size - 1), RED)
        images.append(image)
    buffer = BytesIO()
    if frames > 1:
        images[0].save(
            buffer, format="GIF", save_all=True, append_images=images[1:], loop=0
        )
    else:
        images[0].save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture
def expected_lingering_timers() -> bool:
    """The mocked MQTT client leaves its periodic housekeeping timer behind."""
    return True


def test_icon_id() -> None:
    """Only `lametric:<digits>` names a gallery icon."""
    assert icon_id("lametric:2422") == 2422
    for value in ("2422", "lametric:", "lametric:12a", "lametric:12345678", None, 7):
        assert icon_id(value) is None


def test_convert() -> None:
    """Icons become 64 pixels and a decodable 8x8 JPEG; transparency is off."""
    icon = convert(png())
    assert len(icon["pixels"]) == 64
    assert icon["pixels"][0] == 0xFF0000
    assert icon["pixels"][1] == 0
    assert not icon["animated"]
    with Image.open(BytesIO(base64.b64decode(icon["jpeg"]))) as image:
        assert image.format == "JPEG"
        assert image.size == (8, 8)

    assert convert(png(frames=2))["animated"]
    assert len(convert(png(16))["pixels"]) == 64


async def test_store_downloads_once_and_resolves(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """An icon is downloaded once, then sent inline instead of its name."""
    aioclient_mock.get(ICON_URL.format(id=2422), content=png())
    aioclient_mock.get(ICON_URL.format(id=404), status=404)
    store = IconStore(hass)
    await store.async_load()

    page = json.dumps({"text": "21°", "icon": "lametric:2422"})
    resolved = json.loads(await store.async_resolve(page))
    assert resolved["text"] == "21°"
    assert resolved["icon"] == convert(png())["jpeg"]
    await store.async_resolve(page)
    assert aioclient_mock.call_count == 1

    # Unavailable icons are dropped rather than sent as an unknown name.
    missing = json.loads(
        await store.async_resolve(json.dumps({"text": "x", "icon": "lametric:404"}))
    )
    assert missing == {"text": "x"}
    # Other names are icons stored on the clock: left alone.
    assert await store.async_resolve('{"icon":"tram"}') == '{"icon":"tram"}'
    assert await store.async_resolve("") == ""


async def test_numbers_are_gallery_icons(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A bare number is the gallery icon, unless the clock has that file."""
    aioclient_mock.get(ICON_URL.format(id=2422), content=png())
    aioclient_mock.get(ICON_URL.format(id=2355), content=png())
    aioclient_mock.get(ICON_URL.format(id=404), status=404)
    aioclient_mock.get(
        "http://192.168.1.161/list", json=[{"type": "file", "name": "2355.jpg"}]
    )
    store = IconStore(hass)
    jpeg = convert(png())["jpeg"]

    # Nowhere to look on the clock: the gallery icon, sent inline.
    for name in ('"2422"', "2422"):
        page = json.loads(await store.async_resolve(f'{{"icon":{name}}}'))
        assert page["icon"] == jpeg
    # Not in the gallery either: left to the clock, rather than dropped.
    assert await store.async_resolve('{"icon":"404"}') == '{"icon":"404"}'

    files = ClockFiles(hass, Awtrix3Driver(PREFIX))
    files.set_address("192.168.1.161")
    # A file of the clock wins over the gallery.
    payload = '{"text":"x","icon":"2355"}'
    assert await store.async_resolve(payload, files) == payload
    page = json.loads(await store.async_resolve('{"icon":"2422"}', files))
    assert page["icon"] == jpeg


async def test_numbers_when_the_clock_cannot_be_listed(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A clock that refuses the listing gets the gallery icon, asked once."""
    aioclient_mock.get(ICON_URL.format(id=2422), content=png())
    aioclient_mock.get("http://192.168.1.50/list", status=401)
    store = IconStore(hass)
    files = ClockFiles(hass, Awtrix3Driver(PREFIX))
    files.set_address("192.168.1.50")
    for _ in range(2):
        page = json.loads(await store.async_resolve('{"icon":"2422"}', files))
        assert page["icon"] == convert(png())["jpeg"]
    listings = [c for c in aioclient_mock.mock_calls if "/list" in str(c[1])]
    assert len(listings) == 1


async def test_search_keeps_only_safe_fields(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Search results are reduced to ids, names and an animation flag."""
    aioclient_mock.get(
        SEARCH_URL,
        json={
            "count_all": 100,
            "icons": [
                {"id": 2422, "name": "Sun", "type": 0, "thumbnail": "javascript:x"},
                {"id": "1; drop", "name": "bad"},
                {"id": 653, "name": "<b>Matrix</b>" * 20, "type": 1},
                "junk",
            ],
        },
    )
    store = IconStore(hass)
    result = await store.async_search("sun", 1)
    # The gallery counts pages from 0.
    url = aioclient_mock.mock_calls[-1][1]
    assert url.query["page"] == "0"
    assert url.query["search"] == "sun"
    assert result["more"] is False
    assert result["icons"][0] == {"id": 2422, "name": "Sun", "animated": False}
    assert result["icons"][1]["id"] == 653
    assert result["icons"][1]["animated"] is True
    assert len(result["icons"][1]["name"]) == 64
    assert len(result["icons"]) == 2


async def test_app_publishes_inline_icon(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    mqtt_mock: MqttMockHAClient,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """A simple app with a gallery icon sends the icon itself to the clock."""
    aioclient_mock.get(ICON_URL.format(id=68432), content=png())
    hass.states.async_set("sensor.battery", "84", {"unit_of_measurement": "%"})
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=PREFIX,
        unique_id=PREFIX,
        data={"firmware": "awtrix3", "prefix": PREFIX},
        subentries_data=[
            ConfigSubentryDataWithId(
                data={
                    "mode": "simple",
                    "app_name": "tesla",
                    "entity_id": "sensor.battery",
                    "icon": "lametric:68432",
                    "color": [255, 255, 255],
                    "progress": True,
                },
                subentry_id="s_tesla",
                subentry_type="app",
                title="tesla",
                unique_id="tesla",
            )
        ],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    payloads = [
        c.args[1]
        for c in mqtt_mock.async_publish.call_args_list
        if c.args[0] == f"{PREFIX}/custom/tesla"
    ]
    page = json.loads(payloads[-1])
    assert page["icon"] == convert(png())["jpeg"]
    assert page["progress"] == 84
    assert page["text"] == "84%"

    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {"type": "awtrix_studio/icons/get", "icon_id": 68432}
    )
    response = await client.receive_json()
    assert response["success"]
    assert response["result"]["pixels"][0] == 0xFF0000
    assert hass.data[DATA_ICONS].get(68432) is not None


@pytest.mark.parametrize(
    ("state", "options", "expected"),
    [
        ("84", {}, 84),
        ("150", {}, 100),
        ("-3", {}, 0),
        ("1.5", {"progress_min": 0, "progress_max": 3}, 50),
        ("unknown", {}, None),
    ],
)
async def test_progress_bar(
    hass: HomeAssistant, state: str, options: dict[str, Any], expected: int | None
) -> None:
    """The bar maps the value between min and max to 0..100."""
    hass.states.async_set("sensor.value", "1")
    hass.states.async_set("sensor.level", state)
    template = build_app_template(
        {
            "entity_id": "sensor.value",
            "color": [10, 20, 30],
            "progress": True,
            "progress_entity": "sensor.level",
            "progress_bg": [1, 2, 3],
            **options,
        }
    )
    page = parse_pages(Template(template, hass).async_render(parse_result=True))[0]
    if expected is None:
        assert "progress" not in page
        return
    assert page["progress"] == expected
    assert page["progressC"] == "#0A141E"
    assert page["progressBC"] == "#010203"


def test_progress_bar_is_validated() -> None:
    """Broken settings never reach a template."""
    with pytest.raises(InvalidSimpleOptionsError):
        build_app_template(
            {"entity_id": "sensor.x", "progress": True, "progress_entity": "x') }}"}
        )
    with pytest.raises(InvalidSimpleOptionsError):
        build_app_template(
            {
                "entity_id": "sensor.x",
                "progress": True,
                "progress_min": 5,
                "progress_max": 5,
            }
        )


async def test_animated_icons_are_stored_on_the_clock(
    hass: HomeAssistant,
    mqtt_mock: MqttMockHAClient,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """An animated icon is uploaded once, then named by its number."""
    gif = png(frames=3)
    aioclient_mock.get(ICON_URL.format(id=57980), content=gif)
    aioclient_mock.get(
        "http://192.168.1.161/list", json=[{"type": "file", "name": "2422.gif"}]
    )
    aioclient_mock.post("http://192.168.1.161/edit", status=200)
    hass.states.async_set("sensor.bikes", "12")
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=PREFIX,
        unique_id=PREFIX,
        data={"firmware": "awtrix3", "prefix": PREFIX},
        subentries_data=[
            ConfigSubentryDataWithId(
                data={
                    "mode": "simple",
                    "app_name": "bike",
                    "entity_id": "sensor.bikes",
                    "icon": "lametric:57980",
                    "color": [255, 255, 255],
                },
                subentry_id="s_bike",
                subentry_type="app",
                title="bike",
                unique_id="bike",
            )
        ],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    def last_page() -> dict[str, Any]:
        payloads = [
            c.args[1]
            for c in mqtt_mock.async_publish.call_args_list
            if c.args[0] == f"{PREFIX}/custom/bike"
        ]
        return json.loads(payloads[-1])

    # The clock's address is not known yet: first image, inline.
    assert last_page()["icon"] == convert(gif)["jpeg"]

    # A public address from MQTT is ignored.
    async_fire_mqtt_message(
        hass, f"{PREFIX}/stats", json.dumps({"uptime": 5, "ip_address": "8.8.8.8"})
    )
    await hass.async_block_till_done()
    assert not any(
        str(call[1]).startswith("http://8.8.8.8") for call in aioclient_mock.mock_calls
    )

    async_fire_mqtt_message(
        hass,
        f"{PREFIX}/stats",
        json.dumps({"uptime": 10, "ip_address": "192.168.1.161"}),
    )
    await hass.async_block_till_done()
    assert last_page()["icon"] == "57980"
    uploads = [c for c in aioclient_mock.mock_calls if c[0].upper() == "POST"]
    assert len(uploads) == 1

    # Already on the clock: not uploaded again.
    await entry.runtime_data.async_republish_all()
    await hass.async_block_till_done()
    assert len([c for c in aioclient_mock.mock_calls if c[0].upper() == "POST"]) == 1


async def test_unreachable_clock_falls_back_to_first_image(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Without the clock's file manager, the first image is sent inline."""
    gif = png(frames=2)
    aioclient_mock.get(ICON_URL.format(id=7), content=gif)
    aioclient_mock.get("http://192.168.1.50/list", status=401)
    store = IconStore(hass)
    files = ClockFiles(hass, Awtrix3Driver(PREFIX))
    files.set_address("192.168.1.50")
    page = json.loads(await store.async_resolve('{"icon":"lametric:7"}', files))
    assert page["icon"] == convert(gif)["jpeg"]


def test_upload_form_keeps_the_path() -> None:
    """The destination path reaches the clock unencoded."""
    writer = upload_form("/ICONS/58478.gif", b"GIF89a")()
    headers = [part.headers["Content-Disposition"] for part, _, _ in writer]
    assert headers == ['form-data; name="data"; filename="/ICONS/58478.gif"']


async def test_ng_plays_animated_icons_inline(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """AWTRIX NG gets the GIF itself, unless the payload would be too large."""
    gif = png(frames=3)
    aioclient_mock.get(ICON_URL.format(id=9), content=gif)
    aioclient_mock.get(ICON_URL.format(id=10), content=png())
    store = IconStore(hass)
    driver = AwtrixNGDriver("awtrixNG")
    icon = convert(gif)

    page = json.loads(await store.async_resolve('{"icon":"lametric:9"}', None, driver))
    assert page["icon"] == f"data:image/gif;base64,{icon['gif']}"

    page = json.loads(await store.async_resolve('{"icon":"lametric:10"}', None, driver))
    assert page["icon"] == f"data:image/jpeg;base64,{convert(png())['jpeg']}"

    big = json.dumps({"icon": "lametric:9", "text": "x" * 8000})
    page = json.loads(await store.async_resolve(big, None, driver))
    assert page["icon"] == f"data:image/jpeg;base64,{icon['jpeg']}"


async def test_ng_icon_limit_counts_the_topic(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The clock's 8192 bytes include the topic: a GIF that only fits without
    it falls back to the first image."""
    aioclient_mock.get(ICON_URL.format(id=9), content=png(frames=3))
    store = IconStore(hass)
    driver = AwtrixNGDriver("awtrixNG")
    topic = driver.app_topic("tram")
    gif_page = json.loads(
        await store.async_resolve('{"icon":"lametric:9"}', None, driver)
    )
    room = driver.max_payload - len(json.dumps(gif_page, separators=(",", ":")))
    # Text that leaves the GIF exactly enough room without the topic.
    text = "x" * (room - len(',"text":""'))
    payload = json.dumps({"icon": "lametric:9", "text": text})

    page = json.loads(await store.async_resolve(payload, None, driver))
    assert page["icon"].startswith("data:image/gif;")
    page = json.loads(await store.async_resolve(payload, None, driver, topic))
    assert page["icon"].startswith("data:image/jpeg;")
