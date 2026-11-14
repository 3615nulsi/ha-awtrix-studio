"""Tests for the files of an AWTRIX NG clock: its icons, and inline icons."""

from __future__ import annotations

import base64
from datetime import timedelta
from io import BytesIO
import json
from typing import Any

import aiohttp
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigSubentryDataWithId
from homeassistant.core import HomeAssistant
from PIL import Image
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_mqtt_message,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
from pytest_homeassistant_custom_component.typing import (
    MqttMockHAClient,
    WebSocketGenerator,
)

from custom_components.awtrix_studio.clock_files import LISTING_MAX_AGE, ClockFiles
from custom_components.awtrix_studio.const import DOMAIN
from custom_components.awtrix_studio.drivers import AwtrixNGDriver
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


async def tick(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: float = 1.1
) -> None:
    """Advance time (past the republish delay) and run what became due."""
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


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
    freezer: FrozenDateTimeFactory,
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
    await tick(hass, freezer)
    assert last_page(mqtt_mock)["icon"] == "57980"
    uploads = [c for c in aioclient_mock.mock_calls if c[0].upper() == "POST"]
    assert len(uploads) == 1


async def test_old_ng_gets_a_bare_jpeg(
    hass: HomeAssistant,
    mqtt_mock: MqttMockHAClient,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
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
    await tick(hass, freezer)
    assert last_page(mqtt_mock)["icon"] == jpeg

    # After an update, the clock's version changes and the form follows.
    async_fire_mqtt_message(hass, f"{PREFIX}/state/device", state("1.2.2"))
    await tick(hass, freezer)
    assert last_page(mqtt_mock)["icon"] == f"data:image/jpeg;base64,{jpeg}"


def listings(aioclient_mock: AiohttpClientMocker) -> int:
    """How many times the clock's /ICONS folder was listed."""
    return sum(
        1
        for c in aioclient_mock.mock_calls
        if c[0].upper() == "GET" and "/api/v1/files" in str(c[1])
    )


async def test_listing_is_read_again_when_old(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """An icon deleted on the clock is no longer taken as present."""
    files = ClockFiles(hass, AwtrixNGDriver(PREFIX))
    files.set_address("192.168.1.86")
    aioclient_mock.get(f"{CLOCK}/api/v1/files", json=LISTING)
    assert await files.async_has("co2") is True
    assert await files.async_has("co2") is True
    assert listings(aioclient_mock) == 1

    # The user deletes co2.gif on the clock.
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{CLOCK}/api/v1/files", json={"files": []})
    assert await files.async_has("co2") is True
    files._listed_at -= LISTING_MAX_AGE + 1
    assert await files.async_has("co2") is False
    assert listings(aioclient_mock) == 1


async def test_restart_reads_the_files_again(
    hass: HomeAssistant,
    mqtt_mock: MqttMockHAClient,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A clock that restarted (maybe re-flashed) has its files listed again."""
    aioclient_mock.get(ICON_URL.format(id=57980), content=gif(frames=3))
    # The listing never shows the uploaded icon: as if the clock lost it.
    aioclient_mock.get(f"{CLOCK}/api/v1/files", json=LISTING)
    aioclient_mock.post(f"{CLOCK}/api/v1/files", json={"ok": True})
    await setup_clock(hass, "lametric:57980")
    async_fire_mqtt_message(hass, f"{PREFIX}/availability", "online")
    async_fire_mqtt_message(hass, f"{PREFIX}/state/device", state())
    await tick(hass, freezer)
    assert listings(aioclient_mock) == 1
    uploads = [c for c in aioclient_mock.mock_calls if c[0].upper() == "POST"]
    assert len(uploads) == 1

    async_fire_mqtt_message(hass, f"{PREFIX}/availability", "offline")
    async_fire_mqtt_message(hass, f"{PREFIX}/availability", "online")
    await tick(hass, freezer)
    assert listings(aioclient_mock) == 2
    uploads = [c for c in aioclient_mock.mock_calls if c[0].upper() == "POST"]
    assert len(uploads) == 2
    assert last_page(mqtt_mock)["icon"] == "57980"


async def test_credentials_are_sent(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Every request carries the web page's user name and password, if set."""
    files = ClockFiles(hass, AwtrixNGDriver(PREFIX))
    files.set_address("192.168.1.86")
    files.set_credentials("admin", "pässword")
    aioclient_mock.get(f"{CLOCK}/api/v1/files", json=LISTING)
    aioclient_mock.post(f"{CLOCK}/api/v1/files", json={"ok": True})
    aioclient_mock.get(f"{CLOCK}/api/v1/apps", json=[])
    aioclient_mock.post(f"{CLOCK}/api/v1/apps/order", json={"ok": True})
    aioclient_mock.get(f"{CLOCK}/ICONS/co2.gif", content=gif())

    assert await files.async_ensure("57980", gif(frames=2), "gif")
    await files.async_get_text("/api/v1/apps")
    await files.async_post_json("/api/v1/apps/order", ["Time"])
    assert await files.async_read_icon("co2") is not None

    expected = "Basic " + base64.b64encode("admin:pässword".encode()).decode()
    assert len(aioclient_mock.mock_calls) == 6
    for call in aioclient_mock.mock_calls:
        assert call[3]["Authorization"] == expected

    # Without a user name, no header at all.
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{CLOCK}/api/v1/apps", json=[])
    files.set_credentials(None, None)
    await files.async_get_text("/api/v1/apps")
    assert "Authorization" not in aioclient_mock.mock_calls[0][3]


@pytest.mark.parametrize(
    ("username", "wording"),
    [(None, "asks for a password"), ("admin", "refused the user name")],
)
async def test_password_warning(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    caplog: pytest.LogCaptureFixture,
    username: str | None,
    wording: str,
) -> None:
    """A 401 says, once, that credentials are set by reconfiguring the clock."""
    files = ClockFiles(hass, AwtrixNGDriver(PREFIX))
    files.set_address("192.168.1.86")
    files.set_credentials(username, "wrong")
    aioclient_mock.get(f"{CLOCK}/api/v1/files", status=401)
    aioclient_mock.get(f"{CLOCK}/api/v1/apps", status=401)

    assert not await files.async_ensure("7", gif(frames=2), "gif")
    with pytest.raises(aiohttp.ClientResponseError):
        await files.async_get_text("/api/v1/apps")
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    assert wording in warnings[0].getMessage()
    assert "reconfiguring the clock" in warnings[0].getMessage()
    assert "wrong" not in caplog.text
