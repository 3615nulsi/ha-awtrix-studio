"""Tests for clocks running AWTRIX NG (and the TC002)."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import json
from typing import Any
from unittest.mock import patch

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigSubentryDataWithId
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_mqtt_message,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)
from pytest_homeassistant_custom_component.typing import (
    MqttMockHAClient,
    WebSocketGenerator,
)

from custom_components.awtrix_studio.const import DOMAIN
from custom_components.awtrix_studio.detect import async_detect_firmware

PREFIX = "awtrixNG"
APP_TOPIC = f"{PREFIX}/cmd/apps/pushed/demo"
CLOCK = "http://192.168.1.42"


@pytest.fixture
def expected_lingering_timers() -> bool:
    """The mocked MQTT client leaves its periodic housekeeping timer behind."""
    return True


def published(mqtt_mock: MqttMockHAClient, topic: str) -> list[str]:
    """Payloads published on a topic, in order."""
    return [
        c.args[1] for c in mqtt_mock.async_publish.call_args_list if c.args[0] == topic
    ]


async def tick(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: float
) -> None:
    """Advance time and run what became due."""
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


def ng_entry(options: dict[str, Any] | None = None) -> MockConfigEntry:
    """A clock running AWTRIX NG with one two-page template app."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=PREFIX,
        unique_id=PREFIX,
        data={"firmware": "awtrix_ng", "prefix": PREFIX},
        options=options or {},
        subentries_data=[
            ConfigSubentryDataWithId(
                data={
                    "mode": "template",
                    "app_name": "demo",
                    "template": '[{"text": "a"}, {"text": "b"}]',
                    "page_interval": 1.5,
                    "cycle_only_on_screen": True,
                    "hide_when_empty": True,
                    "lifetime": 60,
                },
                subentry_id="app_demo",
                subentry_type="app",
                title="demo",
                unique_id="demo",
            )
        ],
    )


async def setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Add and set up a clock."""
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_apps_use_ng_topics(
    hass: HomeAssistant, mqtt_mock: MqttMockHAClient, freezer: FrozenDateTimeFactory
) -> None:
    """Apps are pushed, follow the app on screen and are removed the NG way."""
    entry = ng_entry()
    await setup(hass, entry)
    assert json.loads(published(mqtt_mock, APP_TOPIC)[-1]) == {
        "text": "a",
        "lifetimeMs": 60000,
    }

    async_fire_mqtt_message(hass, f"{PREFIX}/state/apps/active", "demo")
    await tick(hass, freezer, 1.6)
    assert json.loads(published(mqtt_mock, APP_TOPIC)[-1])["text"] == "b"

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert published(mqtt_mock, APP_TOPIC)[-1] == ""


async def test_startup_republishes_once(
    hass: HomeAssistant, mqtt_mock: MqttMockHAClient, freezer: FrozenDateTimeFactory
) -> None:
    """The broker, the address and the version arriving together: one republish."""
    entry = ng_entry()
    await setup(hass, entry)
    count = len(published(mqtt_mock, APP_TOPIC))

    entry.runtime_data._on_connection(True)
    async_fire_mqtt_message(
        hass,
        f"{PREFIX}/state/device",
        json.dumps({"version": "1.2.2", "ipAddress": "192.168.1.42"}),
    )
    async_fire_mqtt_message(hass, f"{PREFIX}/availability", "offline")
    async_fire_mqtt_message(hass, f"{PREFIX}/availability", "online")
    await hass.async_block_till_done()
    assert len(published(mqtt_mock, APP_TOPIC)) == count
    await tick(hass, freezer, 1.1)
    assert len(published(mqtt_mock, APP_TOPIC)) == count + 1
    await tick(hass, freezer, 10)
    assert len(published(mqtt_mock, APP_TOPIC)) == count + 1

    # A later restart is still noticed.
    async_fire_mqtt_message(hass, f"{PREFIX}/availability", "offline")
    async_fire_mqtt_message(hass, f"{PREFIX}/availability", "online")
    await tick(hass, freezer, 1.1)
    assert len(published(mqtt_mock, APP_TOPIC)) == count + 2


async def test_unload_cancels_a_pending_republish(
    hass: HomeAssistant, mqtt_mock: MqttMockHAClient, freezer: FrozenDateTimeFactory
) -> None:
    """Nothing is published for a clock that was unloaded meanwhile."""
    entry = ng_entry()
    await setup(hass, entry)
    count = len(published(mqtt_mock, APP_TOPIC))
    entry.runtime_data._on_connection(True)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await tick(hass, freezer, 5)
    assert len(published(mqtt_mock, APP_TOPIC)) == count


async def test_back_online_republishes(
    hass: HomeAssistant, mqtt_mock: MqttMockHAClient, freezer: FrozenDateTimeFactory
) -> None:
    """The clock coming back online gets its apps again (TC002 restarts too)."""
    await setup(hass, ng_entry())
    count = len(published(mqtt_mock, APP_TOPIC))

    # The retained "online" seen at start is not a restart.
    async_fire_mqtt_message(hass, f"{PREFIX}/availability", "online")
    await hass.async_block_till_done()
    assert len(published(mqtt_mock, APP_TOPIC)) == count

    async_fire_mqtt_message(hass, f"{PREFIX}/availability", "offline")
    await hass.async_block_till_done()
    async_fire_mqtt_message(hass, f"{PREFIX}/availability", "online")
    await tick(hass, freezer, 1.1)
    assert len(published(mqtt_mock, APP_TOPIC)) == count + 1


async def test_order_is_sent_once_over_mqtt(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    mqtt_mock: MqttMockHAClient,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """NG stores the order: it goes once, with the apps switched off on the clock."""
    aioclient_mock.get(
        f"{CLOCK}/api/v1/apps",
        json=[
            {"name": "Time", "enabled": True, "inLoop": True, "slot": 0},
            {"name": "demo", "enabled": True, "inLoop": True, "slot": 1},
            {"name": "Date", "enabled": False, "inLoop": False, "slot": None},
        ],
    )
    order_topic = f"{PREFIX}/cmd/apps/order"
    entry = ng_entry({"app_order": ["demo", "Time"]})
    await setup(hass, entry)
    async_fire_mqtt_message(
        hass, f"{PREFIX}/state/device", json.dumps({"ipAddress": "192.168.1.42"})
    )
    await tick(hass, freezer, 3)
    # The clock already has the order saved before Home Assistant started.
    assert published(mqtt_mock, order_topic) == []

    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {
            "type": "awtrix_studio/order",
            "entry_id": entry.entry_id,
            "order": ["co2", "Time", "demo"],
        }
    )
    assert (await client.receive_json())["success"]
    await tick(hass, freezer, 0.1)
    [payload] = published(mqtt_mock, order_topic)
    # The missing app keeps its place for later; Date stays switched off.
    assert json.loads(payload) == {
        "order": ["co2", "Time", "demo"],
        "disabled": ["Date"],
    }
    assert entry.runtime_data.loop == ["Time", "demo"]

    # An app coming back or a minute passing does not send it again.
    await entry.runtime_data.async_republish_all()
    await tick(hass, freezer, 61)
    assert len(published(mqtt_mock, order_topic)) == 1


async def test_screen_mirror_of_any_size(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    mqtt_mock: MqttMockHAClient,
) -> None:
    """A TC002 screen reaches the panel with its size."""
    entry = ng_entry()
    await setup(hass, entry)
    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {"type": "awtrix_studio/screen", "entry_id": entry.entry_id}
    )
    assert (await client.receive_json())["success"]
    await hass.async_block_till_done()
    assert published(mqtt_mock, f"{PREFIX}/cmd/screen/get") == [""]

    async_fire_mqtt_message(
        hass,
        f"{PREFIX}/state/screen",
        json.dumps({"width": 52, "height": 16, "pixels": [0x00FF00] * 832}),
    )
    await hass.async_block_till_done()
    event = (await client.receive_json())["event"]
    assert (event["width"], event["height"], len(event["pixels"])) == (52, 16, 832)


async def test_detect_firmware(
    hass: HomeAssistant, mqtt_mock: MqttMockHAClient
) -> None:
    """Each firmware shows itself on its own topics; silence means unknown."""

    async def started(prefix: str) -> asyncio.Task[str | None]:
        task = hass.async_create_task(async_detect_firmware(hass, prefix, timeout=1))
        # Subscribed once the screen request is out.
        while not published(mqtt_mock, f"{prefix}/sendscreen"):
            await asyncio.sleep(0)
        return task

    task = await started(PREFIX)
    async_fire_mqtt_message(hass, f"{PREFIX}/state/prefix", PREFIX)
    assert await task == "awtrix_ng"

    task = await started("awtrix_1f")
    async_fire_mqtt_message(hass, "awtrix_1f/screen", "[0]")
    assert await task == "awtrix3"

    assert await async_detect_firmware(hass, "nobody", timeout=0.05) is None


async def test_flow_detects_the_firmware(
    hass: HomeAssistant, mqtt_mock: MqttMockHAClient
) -> None:
    """ "Detect automatically" asks the clock, and says so when it is silent."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    target = "custom_components.awtrix_studio.config_flow.async_detect_firmware"
    with patch(target, return_value=None):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"firmware": "auto", "prefix": PREFIX}
        )
    assert result["errors"] == {"firmware": "not_detected"}

    with patch(target, return_value="awtrix_ng"):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"firmware": "auto", "prefix": PREFIX}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {"firmware": "awtrix_ng", "prefix": PREFIX}


async def test_simple_app_speaks_ng(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    mqtt_mock: MqttMockHAClient,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A simple app with display options reaches an NG clock in NG keys."""
    hass.states.async_set("sensor.temp", "21.5", {"unit_of_measurement": "°C"})
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=PREFIX,
        unique_id=PREFIX,
        data={"firmware": "awtrix_ng", "prefix": PREFIX},
        subentries_data=[
            ConfigSubentryDataWithId(
                data={
                    "mode": "simple",
                    "app_name": "temp",
                    "entity_id": "sensor.temp",
                    "color": [0, 170, 255],
                    "display": {"duration": 10, "rainbow": True, "overlay": "snow"},
                },
                subentry_id="app_temp",
                subentry_type="app",
                title="temp",
                unique_id="temp",
            )
        ],
    )
    await setup(hass, entry)
    topic = f"{PREFIX}/cmd/apps/pushed/temp"
    assert json.loads(published(mqtt_mock, topic)[-1]) == {
        "durationMs": 10000,
        "palette": "Rainbow",
        "overlay": "snow",
        "text": "21.5°C",
        "textColor": "palette",
    }

    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {"type": "awtrix_studio/subscribe", "entry_id": entry.entry_id}
    )
    assert (await client.receive_json())["success"]
    await tick(hass, freezer, 0.2)
    await client.receive_json()

    async_fire_mqtt_message(
        hass,
        f"{PREFIX}/state/capabilities",
        json.dumps(
            {
                "effects": ["Plasma"],
                "overlays": ["snow"],
                "palettes": ["Lava"],
                "display": {"width": 52, "height": 16},
            }
        ),
    )
    async_fire_mqtt_message(
        hass,
        f"{topic}/result",
        json.dumps(
            {"ok": False, "error": {"field": "palette", "message": "out of range"}}
        ),
    )
    await tick(hass, freezer, 0.2)
    snapshot = (await client.receive_json())["event"]
    assert snapshot["capabilities"]["effects"] == ["Plasma"]
    # A TC002 draws apps at double size, on a 26 x 8 grid.
    assert snapshot["display"] == {
        "width": 52,
        "height": 16,
        "scale": 2,
        "known": True,
    }
    [item] = snapshot["items"]
    assert item["clock_error"] == "palette: out of range"

    async_fire_mqtt_message(hass, f"{topic}/result", '{"ok":true}')
    async_fire_mqtt_message(hass, f"{PREFIX}/state/settings", '{"enlargeApps":false}')
    await tick(hass, freezer, 0.2)
    snapshot = (await client.receive_json())["event"]
    assert snapshot["items"][0]["clock_error"] is None
    assert snapshot["display"]["scale"] == 1


async def test_display_size_survives_a_reload(
    hass: HomeAssistant,
    mqtt_mock: MqttMockHAClient,
) -> None:
    """The panel keeps its layouts while a reloaded clock waits for MQTT."""
    entry = ng_entry()
    await setup(hass, entry)
    # Nothing heard yet: the size is a guess, and the snapshot says so.
    assert entry.runtime_data.display.known is False

    async_fire_mqtt_message(
        hass,
        f"{PREFIX}/state/capabilities",
        json.dumps({"display": {"width": 52, "height": 16}}),
    )
    await hass.async_block_till_done()
    assert entry.runtime_data.display.known is True

    # A reload builds a new manager; the retained messages are not delivered
    # again by the mocked client, so the size can only come from the cache.
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    display = entry.runtime_data.display
    assert (display.width, display.height, display.known) == (52, 16, True)


def sized_entry() -> MockConfigEntry:
    """An NG clock with one app whose text is as long as `input_number.n`."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=PREFIX,
        unique_id=PREFIX,
        data={"firmware": "awtrix_ng", "prefix": PREFIX},
        subentries_data=[
            ConfigSubentryDataWithId(
                data={
                    "mode": "template",
                    "app_name": "demo",
                    "template": "{{ {'text': 'x' * (states('input_number.n')"
                    " | int(0))} | tojson }}",
                    "page_interval": 10,
                    "cycle_only_on_screen": True,
                    "hide_when_empty": True,
                    "lifetime": 0,
                },
                subentry_id="app_demo",
                subentry_type="app",
                title="demo",
                unique_id="demo",
            )
        ],
    )


async def set_length(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, length: int
) -> None:
    """Change the app's text length and let it publish."""
    hass.states.async_set("input_number.n", str(length))
    await hass.async_block_till_done()
    await tick(hass, freezer, 2)


# The largest text that fits: 8187 bytes with the topic and `{"text":""}`
# (the clock's 8192 bytes count the 5 bytes of the MQTT packet header too).
FITS = 8187 - len(APP_TOPIC) - len('{"text":""}')


async def test_oversized_payload_is_not_sent(
    hass: HomeAssistant,
    mqtt_mock: MqttMockHAClient,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """NG drops a message over 8187 bytes, topic included: it is not sent, and
    the panel says why until a payload fits again."""
    hass.states.async_set("input_number.n", str(FITS))
    entry = sized_entry()
    await setup(hass, entry)
    app = entry.runtime_data.apps["app_demo"]
    assert len(published(mqtt_mock, APP_TOPIC)[-1]) == FITS + len('{"text":""}')
    assert app.clock_error is None
    count = len(published(mqtt_mock, APP_TOPIC))

    await set_length(hass, freezer, FITS + 1)
    assert len(published(mqtt_mock, APP_TOPIC)) == count
    assert app.clock_error == (
        "message too large: 8188 bytes with its topic (limit 8187)"
    )
    assert caplog.text.count("Not sending app 'demo'") == 1

    # The clock's answer to an older payload does not hide the problem.
    async_fire_mqtt_message(hass, f"{APP_TOPIC}/result", '{"ok":true}')
    await hass.async_block_till_done()
    assert app.clock_error is not None
    # Sent again unchanged: still one warning.
    await entry.runtime_data.async_republish_all()
    assert caplog.text.count("Not sending app 'demo'") == 1
    assert len(published(mqtt_mock, APP_TOPIC)) == count

    # Back under the limit: sent, the error goes, the clock's answers count.
    await set_length(hass, freezer, 3)
    assert published(mqtt_mock, APP_TOPIC)[-1] == '{"text":"xxx"}'
    assert app.clock_error is None
    async_fire_mqtt_message(
        hass,
        f"{APP_TOPIC}/result",
        json.dumps({"ok": False, "error": {"message": "nope"}}),
    )
    await hass.async_block_till_done()
    assert app.clock_error == "nope"


async def test_oversized_test_payload(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    mqtt_mock: MqttMockHAClient,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A test too large for the clock is not sent and says so; the app keeps
    its content and its status."""
    hass.states.async_set("input_number.n", "3")
    entry = sized_entry()
    await setup(hass, entry)
    app = entry.runtime_data.apps["app_demo"]
    count = len(published(mqtt_mock, APP_TOPIC))

    client = await hass_ws_client(hass)
    for subentry in ({"subentry_id": "app_demo"}, {}):
        await client.send_json_auto_id(
            {
                "type": "awtrix_studio/test",
                "entry_id": entry.entry_id,
                "kind": "app",
                **subentry,
                "data": {
                    "mode": "template",
                    "app_name": "demo" if subentry else "other",
                    "template": json.dumps({"text": "y" * 9000}),
                },
            }
        )
        result = (await client.receive_json())["result"]
        assert result["errors"] == {"base": "payload_too_large"}
        assert "limit 8187" in result["detail"]
    assert len(published(mqtt_mock, APP_TOPIC)) == count
    assert published(mqtt_mock, f"{PREFIX}/cmd/apps/pushed/other") == []
    assert app.clock_error is None
    # Nothing to restore later.
    await tick(hass, freezer, 16)
    assert len(published(mqtt_mock, APP_TOPIC)) == count


async def test_error_events(
    hass: HomeAssistant,
    mqtt_mock: MqttMockHAClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`event/error` names the app it refused; others are only logged."""
    await setup(hass, ng_entry())
    app = next(iter(hass.config_entries.async_entries(DOMAIN))).runtime_data.apps[
        "app_demo"
    ]
    error = {"code": "validationFailed", "message": "bad", "field": "icon"}
    event = {"source": "mqtt", "request": APP_TOPIC, "error": error}
    async_fire_mqtt_message(hass, f"{PREFIX}/event/error", json.dumps(event))
    await hass.async_block_till_done()
    assert app.clock_error == "icon: bad"
    # The same refusal on `/result` is not reported twice.
    async_fire_mqtt_message(
        hass, f"{APP_TOPIC}/result", json.dumps({"ok": False, "error": error})
    )
    await hass.async_block_till_done()
    assert caplog.text.count("The clock refused app 'demo'") == 1

    async_fire_mqtt_message(
        hass,
        f"{PREFIX}/event/error",
        json.dumps({**event, "request": f"{PREFIX}/cmd/notify"}),
    )
    async_fire_mqtt_message(hass, f"{PREFIX}/event/error", "not json")
    await hass.async_block_till_done()
    assert app.clock_error == "icon: bad"
    async_fire_mqtt_message(hass, f"{APP_TOPIC}/result", '{"ok":true}')
    await hass.async_block_till_done()
    assert app.clock_error is None
