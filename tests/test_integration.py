"""End-to-end tests: config flows, MQTT, switch, reloads."""

from __future__ import annotations

from datetime import timedelta
import json
from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState, ConfigSubentryDataWithId
from homeassistant.const import ATTR_ENTITY_ID, SERVICE_TURN_OFF, SERVICE_TURN_ON
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_mqtt_message,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.typing import MqttMockHAClient

from custom_components.awtrix_studio.const import DOMAIN

PREFIX = "awtrix_test"
APP_TOPIC = f"{PREFIX}/custom/demo"
SWITCH = "switch.awtrix_test_studio_demo"

APP_DATA: dict[str, Any] = {
    "app_name": "demo",
    "template": '[{"text": "a"}, {"text": "b"}]',
    "page_interval": 1.5,
    "cycle_only_on_screen": True,
    "hide_when_empty": True,
    "lifetime": 0,
}


def published(mqtt_mock: MqttMockHAClient, topic: str = APP_TOPIC) -> list[str]:
    """Return the payloads published on a topic, in order."""
    return [
        call.args[1]
        for call in mqtt_mock.async_publish.call_args_list
        if call.args[0] == topic
    ]


@pytest.fixture
def expected_lingering_timers() -> bool:
    """The mocked MQTT client leaves its periodic housekeeping timer behind."""
    return True


@pytest.fixture
def mock_entry() -> MockConfigEntry:
    """A clock with one two-page app."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=PREFIX,
        unique_id=PREFIX,
        data={"firmware": "awtrix3", "prefix": PREFIX},
        subentries_data=[
            ConfigSubentryDataWithId(
                data=APP_DATA,
                subentry_id="app_demo",
                subentry_type="app",
                title="demo",
                unique_id="demo",
            )
        ],
    )


async def setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Add and set up the entry."""
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def tick(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: float
) -> None:
    """Advance time and run what became due."""
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


# --------------------------------------------------------------- config flow


async def test_flow_requires_mqtt(hass: HomeAssistant) -> None:
    """Without MQTT there is nothing to talk through."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "mqtt_not_configured"


async def test_flow_creates_clock(
    hass: HomeAssistant, mqtt_mock: MqttMockHAClient
) -> None:
    """A clock is identified by its prefix."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"firmware": "awtrix3", "prefix": " bad prefix "}
    )
    assert result["errors"] == {"prefix": "invalid_prefix"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"firmware": "awtrix3", "prefix": f"/{PREFIX}/"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == PREFIX
    assert result["data"] == {"firmware": "awtrix3", "prefix": PREFIX}

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"firmware": "awtrix3", "prefix": PREFIX}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_subentry_flow_adds_app(
    hass: HomeAssistant, mqtt_mock: MqttMockHAClient
) -> None:
    """Adding an app validates it, then publishes it."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=PREFIX,
        unique_id=PREFIX,
        data={"firmware": "awtrix3", "prefix": PREFIX},
    )
    await setup_entry(hass, entry)

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "app"), context={"source": "user"}
    )
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": "template"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "template"

    bad = {**APP_DATA, "app_name": "no spaces"}
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], bad
    )
    assert result["errors"] == {"app_name": "invalid_app_name"}

    bad = {**APP_DATA, "template": "{{ 'not json' }}"}
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], bad
    )
    assert result["errors"] == {"template": "invalid_pages"}

    # Syntax errors are caught by the template selector itself.
    with pytest.raises(InvalidData):
        await hass.config_entries.subentries.async_configure(
            result["flow_id"], {**APP_DATA, "template": "{{ broken"}
        )

    # Errors that only show up when rendering are caught by the flow.
    bad = {**APP_DATA, "template": "{{ 'abc' | int }}"}
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], bad
    )
    assert result["errors"] == {"template": "invalid_template"}

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], APP_DATA
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    assert published(mqtt_mock) == ['{"text":"a"}']
    assert hass.states.get(SWITCH).state == "on"

    # Same name twice is refused.
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "app"), context={"source": "user"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": "template"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], APP_DATA
    )
    assert result["errors"] == {"app_name": "app_name_exists"}


async def test_subentry_reconfigure_keeps_name(
    hass: HomeAssistant, mqtt_mock: MqttMockHAClient, mock_entry: MockConfigEntry
) -> None:
    """Editing an app changes its template, not its name."""
    await setup_entry(hass, mock_entry)

    result = await mock_entry.start_subentry_reconfigure_flow(hass, "app_demo")
    assert result["type"] is FlowResultType.FORM
    new_data = {k: v for k, v in APP_DATA.items() if k != "app_name"}
    new_data["template"] = '{"text": "edited"}'
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], new_data
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()

    subentry = mock_entry.subentries["app_demo"]
    assert subentry.data["app_name"] == "demo"
    assert published(mqtt_mock)[-1] == '{"text":"edited"}'


async def test_subentry_reconfigure_keeps_display_options(
    hass: HomeAssistant, mqtt_mock: MqttMockHAClient
) -> None:
    """Display options are edited in the panel and survive the HA form."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=PREFIX,
        unique_id=PREFIX,
        data={"firmware": "awtrix3", "prefix": PREFIX},
        subentries_data=[
            ConfigSubentryDataWithId(
                data={**APP_DATA, "mode": "template", "display": {"duration": 12}},
                subentry_id="app_demo",
                subentry_type="app",
                title="demo",
                unique_id="demo",
            )
        ],
    )
    await setup_entry(hass, entry)
    assert json.loads(published(mqtt_mock)[-1]) == {"duration": 12, "text": "a"}

    result = await entry.start_subentry_reconfigure_flow(hass, "app_demo")
    new_data = {k: v for k, v in APP_DATA.items() if k != "app_name"}
    new_data["template"] = '{"text": "edited"}'
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], new_data
    )
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    assert entry.subentries["app_demo"].data["display"] == {"duration": 12}
    assert json.loads(published(mqtt_mock)[-1]) == {"duration": 12, "text": "edited"}


async def test_subentry_reconfigure_keeps_segments(
    hass: HomeAssistant, mqtt_mock: MqttMockHAClient
) -> None:
    """The HA form edits the first entity of a line and keeps the others."""
    hass.states.async_set("sensor.up", "750")
    hass.states.async_set("sensor.down", "30")
    line = {
        "mode": "simple",
        "app_name": "net",
        "entity_id": "sensor.up",
        "label": "UP",
        "segments": [{"entity_id": "sensor.down", "label": "DN"}],
        "separator": " | ",
    }
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=PREFIX,
        unique_id=PREFIX,
        data={"firmware": "awtrix3", "prefix": PREFIX},
        subentries_data=[
            ConfigSubentryDataWithId(
                data=line,
                subentry_id="app_net",
                subentry_type="app",
                title="net",
                unique_id="net",
            )
        ],
    )
    await setup_entry(hass, entry)

    result = await entry.start_subentry_reconfigure_flow(hass, "app_net")
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": "simple"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {"entity_id": "sensor.up", "label": "U", "color": [0, 255, 0]},
    )
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    data = entry.subentries["app_net"].data
    assert data["segments"] == line["segments"]
    assert data["separator"] == " | "
    assert json.loads(published(mqtt_mock, f"{PREFIX}/custom/net")[-1]) == {
        "text": [{"t": "U 750", "c": "00FF00"}, {"t": " | DN 30", "c": "FFFFFF"}]
    }


# ------------------------------------------------------------------- runtime


async def test_pages_follow_current_app(
    hass: HomeAssistant,
    mqtt_mock: MqttMockHAClient,
    mock_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Pages alternate only while the clock shows the app."""
    await setup_entry(hass, mock_entry)
    assert published(mqtt_mock) == ['{"text":"a"}']

    async_fire_mqtt_message(hass, f"{PREFIX}/stats/currentApp", "demo")
    await hass.async_block_till_done()
    await tick(hass, freezer, 1.6)
    assert published(mqtt_mock) == ['{"text":"a"}', '{"text":"b"}']

    async_fire_mqtt_message(hass, f"{PREFIX}/stats/currentApp", "Time")
    await hass.async_block_till_done()
    await tick(hass, freezer, 5)
    assert published(mqtt_mock) == ['{"text":"a"}', '{"text":"b"}', '{"text":"a"}']


async def test_clock_reboot_republishes(
    hass: HomeAssistant, mqtt_mock: MqttMockHAClient, mock_entry: MockConfigEntry
) -> None:
    """A lower uptime means the clock restarted and lost its apps."""
    await setup_entry(hass, mock_entry)

    async_fire_mqtt_message(hass, f"{PREFIX}/stats", '{"uptime": 500}')
    await hass.async_block_till_done()
    async_fire_mqtt_message(hass, f"{PREFIX}/stats", '{"uptime": 510}')
    await hass.async_block_till_done()
    assert published(mqtt_mock) == ['{"text":"a"}']

    async_fire_mqtt_message(hass, f"{PREFIX}/stats", '{"uptime": 3}')
    await hass.async_block_till_done()
    assert published(mqtt_mock) == ['{"text":"a"}', '{"text":"a"}']


async def test_switch_and_refresh_service(
    hass: HomeAssistant, mqtt_mock: MqttMockHAClient, mock_entry: MockConfigEntry
) -> None:
    """The switch removes and restores the app; refresh republishes."""
    await setup_entry(hass, mock_entry)

    await hass.services.async_call(
        "switch", SERVICE_TURN_OFF, {ATTR_ENTITY_ID: SWITCH}, blocking=True
    )
    assert hass.states.get(SWITCH).state == "off"
    assert published(mqtt_mock) == ['{"text":"a"}', ""]

    # Refresh ignores disabled apps.
    await hass.services.async_call(DOMAIN, "refresh", blocking=True)
    assert published(mqtt_mock) == ['{"text":"a"}', ""]

    await hass.services.async_call(
        "switch", SERVICE_TURN_ON, {ATTR_ENTITY_ID: SWITCH}, blocking=True
    )
    await hass.async_block_till_done()
    assert published(mqtt_mock) == ['{"text":"a"}', "", '{"text":"a"}']

    await hass.services.async_call(DOMAIN, "refresh", blocking=True)
    assert published(mqtt_mock)[-1] == '{"text":"a"}'
    assert len(published(mqtt_mock)) == 4


async def test_switch_state_survives_reload(
    hass: HomeAssistant, mqtt_mock: MqttMockHAClient, mock_entry: MockConfigEntry
) -> None:
    """A disabled app stays disabled (and off the clock) after a reload."""
    await setup_entry(hass, mock_entry)
    await hass.services.async_call(
        "switch", SERVICE_TURN_OFF, {ATTR_ENTITY_ID: SWITCH}, blocking=True
    )

    assert await hass.config_entries.async_reload(mock_entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get(SWITCH).state == "off"
    assert '{"text":"a"}' not in published(mqtt_mock)[2:]


async def test_removing_app_removes_it_from_clock(
    hass: HomeAssistant,
    mqtt_mock: MqttMockHAClient,
    mock_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Deleting the subentry deletes the app and its switch."""
    await setup_entry(hass, mock_entry)

    assert hass.config_entries.async_remove_subentry(mock_entry, "app_demo")
    await hass.async_block_till_done()

    assert published(mqtt_mock)[-1] == ""
    assert entity_registry.async_get(SWITCH) is None
    assert mock_entry.state is ConfigEntryState.LOADED


async def test_removing_clock_removes_apps(
    hass: HomeAssistant, mqtt_mock: MqttMockHAClient, mock_entry: MockConfigEntry
) -> None:
    """Deleting the clock clears its apps."""
    await setup_entry(hass, mock_entry)

    await hass.config_entries.async_remove(mock_entry.entry_id)
    await hass.async_block_till_done()

    assert published(mqtt_mock)[-1] == ""


async def test_unload_leaves_apps(
    hass: HomeAssistant, mqtt_mock: MqttMockHAClient, mock_entry: MockConfigEntry
) -> None:
    """Unloading (e.g. a restart) does not blank the clock."""
    await setup_entry(hass, mock_entry)

    assert await hass.config_entries.async_unload(mock_entry.entry_id)
    await hass.async_block_till_done()

    assert published(mqtt_mock) == ['{"text":"a"}']
    assert mock_entry.state is ConfigEntryState.NOT_LOADED


# ------------------------------------------------- simple mode and indicators


async def test_simple_app_flow_and_conversion(
    hass: HomeAssistant, mqtt_mock: MqttMockHAClient
) -> None:
    """A simple app publishes its generated page and can become a template."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=PREFIX,
        unique_id=PREFIX,
        data={"firmware": "awtrix3", "prefix": PREFIX},
    )
    await setup_entry(hass, entry)
    hass.states.async_set("sensor.co2", "950", {"unit_of_measurement": "ppm"})

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "app"), context={"source": "user"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": "simple"}
    )
    assert result["step_id"] == "simple"

    simple = {
        "app_name": "co2",
        "entity_id": "sensor.co2",
        "icon": "co2",
        "color": [136, 212, 152],
        "decimals": 0,
        "show_unit": True,
        "hide_when_zero": False,
        "threshold_1": 800,
    }
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], simple
    )
    assert result["errors"] == {"color_1": "color_required"}

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {**simple, "color_1": [255, 209, 128]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    topic = f"{PREFIX}/custom/co2"
    payloads = published(mqtt_mock, topic)
    assert len(payloads) == 1
    assert json.loads(payloads[0]) == {
        "text": "950 ppm",
        "color": "#FFD180",
        "icon": "co2",
    }

    # Editing offers to convert the app to template mode, pre-filled.
    subentry_id = next(iter(entry.subentries))
    result = await entry.start_subentry_reconfigure_flow(hass, subentry_id)
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": "convert"}
    )
    assert result["step_id"] == "template"
    suggested = {
        str(key): key.description["suggested_value"]
        for key in result["data_schema"].schema
        if key.description and "suggested_value" in key.description
    }
    assert suggested["template"].startswith("{#- Generated by Awtrix Studio")

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            "template": suggested["template"],
            "page_interval": 1.5,
            "cycle_only_on_screen": True,
            "hide_when_empty": True,
            "lifetime": 0,
        },
    )
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    assert entry.subentries[subentry_id].data["mode"] == "template"
    assert entry.subentries[subentry_id].data["app_name"] == "co2"


async def test_indicators(
    hass: HomeAssistant,
    mqtt_mock: MqttMockHAClient,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Indicators publish to their own topic and turn off with an empty payload."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=PREFIX,
        unique_id=PREFIX,
        data={"firmware": "awtrix3", "prefix": PREFIX},
    )
    await setup_entry(hass, entry)
    hass.states.async_set("input_boolean.mail", "off")
    hass.states.async_set("sensor.grid", "-1.2")

    # Simple indicator 3: lit while there is mail.
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "indicator"), context={"source": "user"}
    )
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": "simple"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            "indicator": "3",
            "entity_id": "input_boolean.mail",
            "active_state": "on",
            "color": [211, 87, 254],
            "effect": "fade",
            "effect_interval": 3000,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    indicator3 = f"{PREFIX}/indicator3"
    assert published(mqtt_mock, indicator3) == [""]
    hass.states.async_set("input_boolean.mail", "on")
    await tick(hass, freezer, 2)  # past the anti-burst cooldown
    assert published(mqtt_mock, indicator3)[0] == ""
    assert json.loads(published(mqtt_mock, indicator3)[1]) == {
        "color": "#D357FE",
        "fade": 3000,
    }
    assert hass.states.get("switch.awtrix_test_studio_indicator_3").state == "on"

    # Template indicator 1: blue while exporting.
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "indicator"), context={"source": "user"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": "template"}
    )
    template = (
        "{% if states('sensor.grid') | float < 0 %}"
        '{"color": [0, 150, 255]}{% else %}{}{% endif %}'
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"indicator": "1", "template": template}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert [json.loads(p) for p in published(mqtt_mock, f"{PREFIX}/indicator1")] == [
        {"color": [0, 150, 255]}
    ]

    # The same indicator cannot be configured twice.
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "indicator"), context={"source": "user"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": "template"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"indicator": "1", "template": "{}"}
    )
    assert result["errors"] == {"indicator": "indicator_in_use"}


async def test_gradient_indicator_flow(
    hass: HomeAssistant, mqtt_mock: MqttMockHAClient
) -> None:
    """The config flow offers preset gradients; custom ones live in the panel."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=PREFIX,
        unique_id=PREFIX,
        data={"firmware": "awtrix3", "prefix": PREFIX},
    )
    await setup_entry(hass, entry)
    hass.states.async_set("sensor.battery", "10")

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "indicator"), context={"source": "user"}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"next_step_id": "simple"}
    )
    form = {
        "indicator": "2",
        "entity_id": "sensor.battery",
        "color_mode": "gradient",
        "gradient": "custom",
        "active_state": "on",
        "color": [255, 140, 0],
        "effect": "none",
        "effect_interval": 1000,
    }
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], form
    )
    assert result["errors"] == {"gradient": "custom_in_panel"}

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {**form, "gradient": "low_bad", "gradient_min": 100, "gradient_max": 10},
    )
    assert result["errors"] == {"gradient_max": "invalid_range"}

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {**form, "gradient": "low_bad", "gradient_min": 10, "gradient_max": 100},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert [json.loads(p) for p in published(mqtt_mock, f"{PREFIX}/indicator2")] == [
        {"color": [255, 0, 0]}
    ]
