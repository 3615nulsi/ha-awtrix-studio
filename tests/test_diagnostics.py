"""Tests for the diagnostics of a clock."""

from __future__ import annotations

import json

from homeassistant.config_entries import ConfigSubentryDataWithId
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_mqtt_message,
)
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant_custom_component.typing import (
    ClientSessionGenerator,
    MqttMockHAClient,
)

from custom_components.awtrix_studio.const import DOMAIN

PREFIX = "awtrixNG"


@pytest.fixture
def expected_lingering_timers() -> bool:
    """The mocked MQTT client leaves its periodic housekeeping timer behind."""
    return True


def clock() -> MockConfigEntry:
    """An NG clock with a password, a template app and a simple indicator."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=PREFIX,
        unique_id=PREFIX,
        data={
            "firmware": "awtrix_ng",
            "prefix": PREFIX,
            "username": "admin",
            "password": "s3cret",
        },
        options={"app_order": ["demo", "Time"]},
        subentries_data=[
            ConfigSubentryDataWithId(
                data={
                    "mode": "template",
                    "app_name": "demo",
                    "template": '[{"text": "{{ \'Alice is home\' }}"}, {"text": "b"}]',
                    "page_interval": 2,
                },
                subentry_id="app_demo",
                subentry_type="app",
                title="demo",
                unique_id="demo",
            ),
            ConfigSubentryDataWithId(
                data={
                    "mode": "simple",
                    "indicator": "2",
                    "entity_id": "binary_sensor.front_door",
                    "active_state": "on",
                    "color": [255, 0, 0],
                },
                subentry_id="indicator_2",
                subentry_type="indicator",
                title="Indicator 2",
                unique_id="indicator:2",
            ),
        ],
    )


async def test_diagnostics(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    mqtt_mock: MqttMockHAClient,
) -> None:
    """The clock's state is there; secrets and rendered content are not."""
    assert await async_setup_component(hass, "diagnostics", {})
    entry = clock()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    async_fire_mqtt_message(
        hass,
        f"{PREFIX}/state/device",
        json.dumps({"version": "1.2.2", "ipAddress": "192.168.1.42"}),
    )
    async_fire_mqtt_message(
        hass,
        f"{PREFIX}/state/capabilities",
        json.dumps(
            {
                "effects": ["Plasma", "Matrix"],
                "overlays": ["snow"],
                "palettes": ["Lava"],
                "display": {"width": 52, "height": 16},
            }
        ),
    )
    async_fire_mqtt_message(hass, f"{PREFIX}/state/apps/active", "demo")
    await hass.async_block_till_done()

    result = await get_diagnostics_for_config_entry(hass, hass_client, entry)
    text = json.dumps(result)
    for secret in ("s3cret", "admin", "192.168.1.42", "Alice", "front_door"):
        assert secret not in text

    assert result["firmware"] == "awtrix_ng"
    assert result["prefix"] == PREFIX
    assert result["order"] == ["demo", "Time"]
    assert result["entry"]["data"] == {
        "firmware": "awtrix_ng",
        "prefix": PREFIX,
        "username": "**REDACTED**",
        "password": "**REDACTED**",
    }
    clock_info = result["clock"]
    assert clock_info["version"] == "1.2.2"
    assert clock_info["address_known"] is True
    assert clock_info["web_credentials"] is True
    assert clock_info["display"] == {"width": 52, "height": 16, "scale": 2}
    assert clock_info["capabilities"] == {"effects": 2, "overlays": 1, "palettes": 1}
    assert clock_info["current_app"] == "demo"

    app, indicator = result["items"]
    assert app == {
        "kind": "app",
        "app_name": "demo",
        "mode": "template",
        "page_interval": 2,
        "fields": ["template"],
        "template_length": len(entry.subentries["app_demo"].data["template"]),
        "enabled": True,
        "on_screen": True,
        "page_count": 2,
        "template_error": None,
        "clock_error": None,
    }
    assert indicator["kind"] == "indicator"
    assert indicator["indicator"] == "2"
    assert indicator["fields"] == ["active_state", "color", "entity_id"]
    assert "template_length" not in indicator


async def test_diagnostics_of_an_unloaded_clock(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    mqtt_mock: MqttMockHAClient,
) -> None:
    """Without a running clock, only the configuration is described."""
    assert await async_setup_component(hass, "diagnostics", {})
    entry = clock()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_unload(entry.entry_id)
    result = await get_diagnostics_for_config_entry(hass, hass_client, entry)
    assert result["clock"] is None
    assert result["entry"]["state"] == "not_loaded"
    assert [item["kind"] for item in result["items"]] == ["app", "indicator"]
    assert "enabled" not in result["items"][0]
