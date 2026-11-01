"""Tests for the preview of icons stored on the clock."""

from __future__ import annotations

from io import BytesIO
import json
from typing import Any

from homeassistant.core import HomeAssistant
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

from custom_components.awtrix_studio.clock_files import MAX_ICON_FILE_BYTES
from custom_components.awtrix_studio.const import DOMAIN
from custom_components.awtrix_studio.icons import MAX_SIDE, IconError, convert

PREFIX = "awtrix_test"
CLOCK = "http://192.168.1.161"


def image(file_format: str = "GIF", size: tuple[int, int] = (8, 8)) -> bytes:
    """An icon: a red pixel top left, black elsewhere."""
    picture = Image.new("RGB", size, (0, 0, 0))
    picture.putpixel((0, 0), (255, 0, 0))
    buffer = BytesIO()
    picture.save(buffer, format=file_format)
    return buffer.getvalue()


def gif() -> bytes:
    """An 8x8 GIF icon."""
    return image()


@pytest.fixture
def expected_lingering_timers() -> bool:
    """The mocked MQTT client leaves its periodic housekeeping timer behind."""
    return True


async def setup_clock(
    hass: HomeAssistant, firmware: str = "awtrix3", *, address: bool = True
) -> MockConfigEntry:
    """A loaded clock without apps, whose address is known."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=PREFIX,
        unique_id=PREFIX,
        data={"firmware": firmware, "prefix": PREFIX},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    if address:
        async_fire_mqtt_message(
            hass,
            f"{PREFIX}/stats",
            json.dumps({"uptime": 10, "ip_address": "192.168.1.161"}),
        )
        await hass.async_block_till_done()
    return entry


async def ask(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator, entry_id: str, name: str
) -> dict[str, Any]:
    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {"type": "awtrix_studio/icons/clock", "entry_id": entry_id, "name": name}
    )
    return await client.receive_json()


async def test_icon_of_the_clock(
    hass: HomeAssistant,
    mqtt_mock: MqttMockHAClient,
    hass_ws_client: WebSocketGenerator,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """The preview gets the pixels of a file of the clock's /ICONS folder."""
    aioclient_mock.get(
        f"{CLOCK}/list",
        json=[
            {"type": "file", "name": "smile.gif"},
            {"type": "file", "name": "notes.txt"},
        ],
    )
    aioclient_mock.get(f"{CLOCK}/ICONS/smile.gif", content=gif())
    entry = await setup_clock(hass)

    response = await ask(hass, hass_ws_client, entry.entry_id, "smile")
    assert response["success"]
    pixels = response["result"]["pixels"]
    assert len(pixels) == 64
    assert pixels[0] == 0xFF0000
    assert pixels[1] == 0

    # No such icon: no pixels, and nothing else is fetched.
    response = await ask(hass, hass_ws_client, entry.entry_id, "notes")
    assert response["success"]
    assert response["result"]["pixels"] is None
    fetched = [str(call[1]) for call in aioclient_mock.mock_calls]
    assert not any("notes" in url for url in fetched)


@pytest.mark.parametrize("name", ["../secret", "a/b", "smile.gif", "", "x" * 65])
async def test_icon_name_is_validated(
    hass: HomeAssistant,
    mqtt_mock: MqttMockHAClient,
    hass_ws_client: WebSocketGenerator,
    aioclient_mock: AiohttpClientMocker,
    name: str,
) -> None:
    """Only plain names reach the clock."""
    entry = await setup_clock(hass)
    response = await ask(hass, hass_ws_client, entry.entry_id, name)
    assert not response["success"]
    assert response["error"]["code"] == "invalid_format"
    assert aioclient_mock.call_count == 0


async def test_icon_file_too_large(
    hass: HomeAssistant,
    mqtt_mock: MqttMockHAClient,
    hass_ws_client: WebSocketGenerator,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """A file over the size limit is not decoded."""
    aioclient_mock.get(f"{CLOCK}/list", json=[{"type": "file", "name": "big.jpg"}])
    aioclient_mock.get(
        f"{CLOCK}/ICONS/big.jpg", content=b"\xff" * (MAX_ICON_FILE_BYTES + 1)
    )
    entry = await setup_clock(hass)
    response = await ask(hass, hass_ws_client, entry.entry_id, "big")
    assert not response["success"]
    assert response["error"]["code"] == "icons_unavailable"


@pytest.mark.parametrize("firmware", ["awtrix3", "awtrix_ng"])
async def test_icon_files_unavailable(
    hass: HomeAssistant,
    mqtt_mock: MqttMockHAClient,
    hass_ws_client: WebSocketGenerator,
    aioclient_mock: AiohttpClientMocker,
    firmware: str,
) -> None:
    """Without the clock's address, nothing is fetched."""
    entry = await setup_clock(hass, firmware, address=False)
    response = await ask(hass, hass_ws_client, entry.entry_id, "smile")
    assert not response["success"]
    assert response["error"]["code"] == "icons_unavailable"
    assert aioclient_mock.call_count == 0


@pytest.mark.parametrize("file_format", ["GIF", "JPEG", "PNG"])
def test_icon_formats(file_format: str) -> None:
    """GIF, JPEG and PNG icons are decoded."""
    assert len(convert(image(file_format))["pixels"]) == 64


@pytest.mark.parametrize(
    "data",
    [
        image("BMP"),
        image("TIFF"),
        image("GIF", (MAX_SIDE + 1, 8)),
        image("PNG", (8, MAX_SIDE + 1)),
        b"GIF89a" + b"\x00" * 32,
    ],
    ids=["bmp", "tiff", "too-wide", "too-tall", "broken"],
)
def test_other_images_are_refused(data: bytes) -> None:
    """Other formats, larger or broken images are refused."""
    with pytest.raises(IconError):
        convert(data)


async def test_large_image_on_the_clock_is_refused(
    hass: HomeAssistant,
    mqtt_mock: MqttMockHAClient,
    hass_ws_client: WebSocketGenerator,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """A small file announcing a huge image is refused before decoding."""
    huge = image("GIF", (9000, 9000))
    assert len(huge) < MAX_ICON_FILE_BYTES
    aioclient_mock.get(f"{CLOCK}/list", json=[{"type": "file", "name": "bomb.gif"}])
    aioclient_mock.get(f"{CLOCK}/ICONS/bomb.gif", content=huge)
    entry = await setup_clock(hass)
    response = await ask(hass, hass_ws_client, entry.entry_id, "bomb")
    assert not response["success"]
    assert response["error"]["code"] == "icons_unavailable"
    assert "too large" in response["error"]["message"]
