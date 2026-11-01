"""Tests for the files of an AWTRIX NG clock: its icons, and inline icons."""

from __future__ import annotations

from io import BytesIO
import json
from typing import Any

from homeassistant.config_entries import ConfigSubentryDataWithId
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

from custom_components.awtrix_studio.const import DOMAIN
from custom_components.awtrix_studio.icons import ICON_URL, convert

PREFIX = "awtrixNG"
CLOCK = "http://192.168.1.86"
TOPIC = f"{PREFIX}/cmd/apps/pushed/bike"
# What a TC002 on AWTRIX NG 1.2.2 answers, shortened.
LISTING = {
    "files": [
        {"name": "co2.gif", "size": 546},
        {"name": "washing-machine-running.gif", "size": 957},
    ],
    "usedBytes": 65819,
    "totalBytes": 6955291,
}


@pytest.fixture
def expected_lingering_timers() -> bool:
    """The mocked MQTT client leaves its periodic housekeeping timer behind."""
    return True


def gif(frames: int = 1, size: int = 8) -> bytes:
    """A GIF: a red pixel top left, then one moving along the bottom."""
    images = []
    for frame in range(frames):
        image = Image.new("RGB", (size, size), (0, 0, 0))
        image.putpixel((0, 0), (255, 0, 0))
        image.putpixel((frame, size - 1), (255, 0, 0))
        images.append(image)
    buffer = BytesIO()
    images[0].save(
        buffer, format="GIF", save_all=frames > 1, append_images=images[1:], loop=0
    )
    return buffer.getvalue()


def state(version: str = "1.2.2") -> str:
    """The device state of the clock (`<prefix>/state/device`)."""
    return json.dumps(
        {"version": version, "uptimeSeconds": 100, "ipAddress": "192.168.1.86"}
    )


async def setup_clock(hass: HomeAssistant, icon: str | None = None) -> MockConfigEntry:
    """An NG clock, with a simple app showing an icon when one is given."""
    subentries = []
    if icon is not None:
        hass.states.async_set("sensor.bikes", "12")
        subentries.append(
            ConfigSubentryDataWithId(
                data={
                    "mode": "simple",
                    "app_name": "bike",
                    "entity_id": "sensor.bikes",
                    "icon": icon,
                    "color": [255, 255, 255],
                },
                subentry_id="s_bike",
                subentry_type="app",
                title="bike",
                unique_id="bike",
            )
        )
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=PREFIX,
        unique_id=PREFIX,
        data={"firmware": "awtrix_ng", "prefix": PREFIX},
        subentries_data=subentries,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def last_page(mqtt_mock: MqttMockHAClient) -> dict[str, Any]:
    payloads = [
        c.args[1] for c in mqtt_mock.async_publish.call_args_list if c.args[0] == TOPIC
    ]
    return json.loads(payloads[-1])


async def test_icons_of_the_clock_in_the_panel(
    hass: HomeAssistant,
    mqtt_mock: MqttMockHAClient,
    hass_ws_client: WebSocketGenerator,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """The panel reads the icons stored on an NG clock, through its files API."""
    aioclient_mock.get(f"{CLOCK}/api/v1/files", json=LISTING)
    aioclient_mock.get(f"{CLOCK}/ICONS/co2.gif", content=gif())
    aioclient_mock.get(
        f"{CLOCK}/ICONS/washing-machine-running.gif", content=gif(4, size=16)
    )
    entry = await setup_clock(hass)
    client = await hass_ws_client(hass)

    async def ask(name: str) -> dict[str, Any]:
        await client.send_json_auto_id(
            {
                "type": "awtrix_studio/icons/clock",
                "entry_id": entry.entry_id,
                "name": name,
            }
        )
        return await client.receive_json()

    # The clock's address is not known yet.
    assert not (await ask("co2"))["success"]

    async_fire_mqtt_message(hass, f"{PREFIX}/state/device", state())
    await hass.async_block_till_done()
    await client.send_json_auto_id(
        {"type": "awtrix_studio/subscribe", "entry_id": entry.entry_id}
    )
    assert (await client.receive_json())["success"]
    snapshot = (await client.receive_json())["event"]
    assert snapshot["features"]["icon_files"] is True

    response = await ask("co2")
    assert response["success"]
    assert response["result"]["pixels"][0] == 0xFF0000
    assert response["result"]["large"] is None
    # A 16x16 icon also comes as is, for a TC002.
    result = (await ask("washing-machine-running"))["result"]
    assert len(result["pixels"]) == 64
    assert len(result["large"]) == 256
    assert result["large"][0] == 0xFF0000
    assert result["large"][1] == 0
    assert (await ask("sun"))["result"]["pixels"] is None
    listings = [c for c in aioclient_mock.mock_calls if "/api/v1/files" in str(c[1])]
    assert listings
    assert all(c[1].query.get("dir") == "/ICONS" for c in listings)


async def test_animated_icons_are_stored_on_the_clock(
    hass: HomeAssistant,
    mqtt_mock: MqttMockHAClient,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """An animated gallery icon is uploaded to the NG clock once, then named."""
    animated = gif(frames=3)
    aioclient_mock.get(ICON_URL.format(id=57980), content=animated)
    aioclient_mock.get(f"{CLOCK}/api/v1/files", json=LISTING)
    aioclient_mock.post(f"{CLOCK}/api/v1/files", json={"ok": True})
    await setup_clock(hass, "lametric:57980")

    # Without the address, the GIF goes inline.
    assert last_page(mqtt_mock)["icon"].startswith("data:image/gif;base64,")

    async_fire_mqtt_message(hass, f"{PREFIX}/state/device", state())
    await hass.async_block_till_done()
    assert last_page(mqtt_mock)["icon"] == "57980"
    uploads = [c for c in aioclient_mock.mock_calls if c[0].upper() == "POST"]
    assert len(uploads) == 1


async def test_old_ng_gets_a_bare_jpeg(
    hass: HomeAssistant,
    mqtt_mock: MqttMockHAClient,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Before 1.2.0, AWTRIX NG reads an inline icon as a bare Base64 JPEG."""
    still = gif()
    aioclient_mock.get(ICON_URL.format(id=7), content=still)
    # The clock's files cannot be listed: the icon goes inline.
    aioclient_mock.get(f"{CLOCK}/api/v1/files", status=404)
    await setup_clock(hass, "lametric:7")
    jpeg = convert(still)["jpeg"]
    assert last_page(mqtt_mock)["icon"] == f"data:image/jpeg;base64,{jpeg}"

    async_fire_mqtt_message(hass, f"{PREFIX}/state/device", state("1.1.2"))
    await hass.async_block_till_done()
    assert last_page(mqtt_mock)["icon"] == jpeg

    # After an update, the clock's version changes and the form follows.
    async_fire_mqtt_message(hass, f"{PREFIX}/state/device", state("1.2.2"))
    await hass.async_block_till_done()
    assert last_page(mqtt_mock)["icon"] == f"data:image/jpeg;base64,{jpeg}"
