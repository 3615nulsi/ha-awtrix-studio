"""Tests for the clock's reconfiguration and the discovery of AWTRIX NG clocks."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

from homeassistant.config_entries import SOURCE_MQTT, ConfigSubentryDataWithId
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.mqtt import MqttServiceInfo
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_mqtt_message,
)
from pytest_homeassistant_custom_component.typing import MqttMockHAClient

from custom_components.awtrix_studio.const import DOMAIN

PREFIX = "awtrix_test"
SWITCH = "switch.awtrix_test_studio_demo"
DETECT = "custom_components.awtrix_studio.config_flow.async_detect_firmware"


@pytest.fixture
def expected_lingering_timers() -> bool:
    """The mocked MQTT client leaves its periodic housekeeping timer behind."""
    return True


def published(mqtt_mock: MqttMockHAClient, topic: str) -> list[str]:
    """Payloads published on a topic, in order."""
    return [
        c.args[1] for c in mqtt_mock.async_publish.call_args_list if c.args[0] == topic
    ]


def clock(
    prefix: str = PREFIX, title: str | None = None, **data: Any
) -> MockConfigEntry:
    """An AWTRIX 3 clock with one app."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=title or prefix,
        unique_id=prefix,
        data={"firmware": "awtrix3", "prefix": prefix, **data},
        subentries_data=[
            ConfigSubentryDataWithId(
                data={
                    "mode": "template",
                    "app_name": "demo",
                    "template": '{"text": "a"}',
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


def suggested(result: dict[str, Any]) -> dict[str, Any]:
    """The values a form is pre-filled with."""
    return {
        str(key): key.description["suggested_value"]
        for key in result["data_schema"].schema
        if key.description and "suggested_value" in key.description
    }


# ---------------------------------------------------------------- reconfigure


async def test_reconfigure_checks_the_input(
    hass: HomeAssistant, mqtt_mock: MqttMockHAClient
) -> None:
    """Bad prefixes, a prefix in use and bad credentials are refused."""
    entry = clock()
    await setup(hass, entry)
    MockConfigEntry(
        domain=DOMAIN,
        title="other",
        unique_id="other",
        data={"firmware": "awtrix3", "prefix": "other"},
    ).add_to_hass(hass)

    result = await entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"
    assert suggested(result) == {
        "firmware": "awtrix3",
        "prefix": PREFIX,
        "username": "",
    }
    form = {"firmware": "awtrix3", "prefix": PREFIX}
    for changes, errors in (
        ({"prefix": "a b"}, {"prefix": "invalid_prefix"}),
        ({"prefix": "/other/"}, {"prefix": "prefix_in_use"}),
        ({"username": "a:b", "password": "x"}, {"username": "invalid_username"}),
        ({"username": "admin", "password": "x" * 65}, {"password": "invalid_password"}),
        ({"password": "secret"}, {"username": "username_required"}),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {**form, **changes}
        )
        assert result["errors"] == errors
        # The password typed is never sent back to the form.
        assert "password" not in suggested(result)

    with patch(DETECT, return_value=None):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {**form, "firmware": "auto"}
        )
    assert result["errors"] == {"firmware": "not_detected"}
    assert entry.data == {"firmware": "awtrix3", "prefix": PREFIX}


async def test_reconfigure_moves_the_apps(
    hass: HomeAssistant, mqtt_mock: MqttMockHAClient
) -> None:
    """A new prefix and firmware: apps leave the old topics for the new ones."""
    entry = clock()
    await setup(hass, entry)
    old_topic = f"{PREFIX}/custom/demo"
    assert published(mqtt_mock, old_topic) == ['{"text":"a"}']

    result = await entry.start_reconfigure_flow(hass)
    with patch(DETECT, return_value="awtrix_ng"):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                "firmware": "auto",
                "prefix": "awtrixNG",
                "username": "admin",
                "password": "secret",
            },
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()

    assert entry.unique_id == "awtrixNG"
    assert entry.title == "awtrixNG"
    assert entry.data == {
        "firmware": "awtrix_ng",
        "prefix": "awtrixNG",
        "username": "admin",
        "password": "secret",
    }
    assert published(mqtt_mock, old_topic) == ['{"text":"a"}', ""]
    assert published(mqtt_mock, "awtrixNG/cmd/apps/pushed/demo") == ['{"text":"a"}']
    assert hass.states.get(SWITCH).state == "on"
    manager = entry.runtime_data
    assert manager.prefix == "awtrixNG"
    assert manager.files.has_credentials

    # An empty password keeps the current one; no reload for credentials.
    result = await entry.start_reconfigure_flow(hass)
    assert suggested(result)["username"] == "admin"
    assert "password" not in suggested(result)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"firmware": "awtrix_ng", "prefix": "awtrixNG", "username": "admin"},
    )
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    assert entry.data["password"] == "secret"
    assert entry.runtime_data is manager

    # Without a user name, the credentials are dropped.
    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"firmware": "awtrix_ng", "prefix": "awtrixNG"}
    )
    await hass.async_block_till_done()
    assert entry.data == {"firmware": "awtrix_ng", "prefix": "awtrixNG"}
    assert entry.runtime_data is manager
    assert not manager.files.has_credentials
    assert len(published(mqtt_mock, "awtrixNG/cmd/apps/pushed/demo")) == 1


async def test_reconfigure_keeps_a_chosen_title(
    hass: HomeAssistant, mqtt_mock: MqttMockHAClient
) -> None:
    """A title the user gave the clock survives a new prefix."""
    entry = clock(title="Kitchen")
    await setup(hass, entry)
    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"firmware": "awtrix3", "prefix": "kitchen"}
    )
    await hass.async_block_till_done()
    assert entry.title == "Kitchen"
    assert entry.unique_id == "kitchen"


async def test_credentials_are_not_logged(
    hass: HomeAssistant,
    mqtt_mock: MqttMockHAClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The password never reaches the log, even at debug level."""
    caplog.set_level("DEBUG")
    entry = clock(username="admin", password="s3cr3t-pw")
    await setup(hass, entry)
    async_fire_mqtt_message(
        hass, f"{PREFIX}/stats", json.dumps({"uptime": 1, "ip_address": "10.0.0.9"})
    )
    await hass.async_block_till_done()
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert "s3cr3t-pw" not in caplog.text


# ------------------------------------------------------------------ discovery


def discovery(topic: str, payload: str | bytes) -> MqttServiceInfo:
    """A retained message as the MQTT integration hands it to the flow."""
    return MqttServiceInfo(
        topic=topic,
        payload=payload,
        qos=0,
        retain=True,
        subscribed_topic="+/state/prefix",
        timestamp=0,
    )


async def test_discovered_ng_clock(
    hass: HomeAssistant, mqtt_mock: MqttMockHAClient
) -> None:
    """An NG clock announcing its prefix is offered, then added as NG."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_MQTT},
        data=discovery("awtrixNG/state/prefix", b"awtrixNG"),
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "discovery_confirm"
    assert result["description_placeholders"] == {"prefix": "awtrixNG"}

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "awtrixNG"
    assert result["data"] == {"firmware": "awtrix_ng", "prefix": "awtrixNG"}
    assert result["result"].unique_id == "awtrixNG"

    # Seen again (e.g. at the next start): already there.
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_MQTT},
        data=discovery("awtrixNG/state/prefix", "awtrixNG"),
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


@pytest.mark.parametrize(
    ("topic", "payload"),
    [
        ("awtrixNG/state/prefix", "other"),
        ("awtrixNG/state/prefix", ""),
        ("awtrixNG/state/prefix", '{"prefix": "awtrixNG"}'),
        ("a b/state/prefix", "a b"),
        ("awtrixNG/state/device", "awtrixNG"),
        (f"{'x' * 65}/state/prefix", "x" * 65),
    ],
)
async def test_discovery_needs_a_matching_prefix(
    hass: HomeAssistant, mqtt_mock: MqttMockHAClient, topic: str, payload: str
) -> None:
    """Anything but the prefix itself on `<prefix>/state/prefix` is ignored."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_MQTT}, data=discovery(topic, payload)
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_awtrix_ng"


async def test_discovery_through_mqtt(
    hass: HomeAssistant, mqtt_mock: MqttMockHAClient
) -> None:
    """The manifest's topic reaches the flow; a configured clock is skipped."""
    await setup(hass, clock())
    async_fire_mqtt_message(hass, "clock2/state/prefix", "clock2", retain=True)
    async_fire_mqtt_message(hass, f"{PREFIX}/state/prefix", PREFIX, retain=True)
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["unique_id"] for flow in flows] == ["clock2"]
    assert flows[0]["step_id"] == "discovery_confirm"
