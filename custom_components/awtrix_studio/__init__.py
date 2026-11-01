"""Awtrix Studio: AWTRIX apps bound to Home Assistant templates."""

from __future__ import annotations

import asyncio
import logging

from homeassistant.components import mqtt
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,
    entity_registry as er,
)
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.typing import ConfigType

from . import websocket
from .const import (
    CONF_APP_ORDER,
    CONF_FIRMWARE,
    CONF_PREFIX,
    DATA_ICONS,
    DOMAIN,
    SERVICE_REFRESH,
)
from .drivers import get_driver
from .icons import IconStore
from .manager import MANAGED_SUBENTRY_TYPES, ClockManager, subentry_topic
from .panel import async_register_panel

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SWITCH]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

# Latest pending reload per clock, to reload once after a burst of changes.
_PENDING_RELOADS: dict[str, object] = {}

type AwtrixStudioConfigEntry = ConfigEntry[ClockManager]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the integration's services."""

    async def _async_refresh(call: ServiceCall) -> None:
        for entry in hass.config_entries.async_loaded_entries(DOMAIN):
            await entry.runtime_data.async_republish_all()

    hass.services.async_register(DOMAIN, SERVICE_REFRESH, _async_refresh)
    icons = IconStore(hass)
    await icons.async_load()
    hass.data[DATA_ICONS] = icons
    websocket.async_setup(hass)
    await async_register_panel(hass)
    return True


async def async_setup_entry(
    hass: HomeAssistant, entry: AwtrixStudioConfigEntry
) -> bool:
    """Set up one clock."""
    if not await mqtt.async_wait_for_mqtt_client(hass):
        raise ConfigEntryNotReady("MQTT is not available")

    manager = ClockManager(
        hass,
        get_driver(entry.data[CONF_FIRMWARE], entry.data[CONF_PREFIX]),
        hass.data[DATA_ICONS],
    )
    for subentry in entry.subentries.values():
        if subentry.subentry_type in MANAGED_SUBENTRY_TYPES:
            manager.add_app(subentry)
    await manager.async_start()
    manager.set_order(entry.options.get(CONF_APP_ORDER), initial=True)
    entry.runtime_data = manager
    _async_tidy_registries(hass, entry)

    # Apps are enabled by their switch, once its last state is restored.
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    async_dispatcher_send(hass, websocket.signal_entry_loaded(entry.entry_id))
    return True


@callback
def _async_tidy_registries(hass: HomeAssistant, entry: AwtrixStudioConfigEntry) -> None:
    """Keep the clock's single device and its switches on the main entry.

    All switches share one device. Home Assistant ties a device to a single
    config subentry, so the switches belong to the entry itself, not to the
    app's subentry. Versions up to 0.6.0 attached them to their subentry, which
    moved the device from one subentry to the next and dropped the switches of
    the previous ones: move the device back and forget those switches.
    """
    dev_reg = dr.async_get(hass)
    # Looked up among the entry's own devices: identifiers are no longer unique
    # across config entries.
    for device in dr.async_entries_for_config_entry(dev_reg, entry.entry_id):
        if (DOMAIN, entry.entry_id) in device.identifiers and (
            getattr(device, "config_subentry_id", None) is not None
        ):
            dev_reg.async_update_device(device.id, new_config_subentry_id=None)

    ent_reg = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
        subentry_id = entity.unique_id.removesuffix("_enabled")
        if entity.config_subentry_id is not None or subentry_id not in entry.subentries:
            ent_reg.async_remove(entity.entity_id)


async def async_unload_entry(
    hass: HomeAssistant, entry: AwtrixStudioConfigEntry
) -> bool:
    """Unload a clock, leaving its apps on the clock until they are replaced."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_stop()
        async_dispatcher_send(hass, websocket.signal_entry_loaded(entry.entry_id))
    return unloaded


async def async_remove_entry(
    hass: HomeAssistant, entry: AwtrixStudioConfigEntry
) -> None:
    """Remove every app and indicator from the clock when it is deleted."""
    if not await mqtt.async_wait_for_mqtt_client(hass):
        return
    driver = get_driver(entry.data[CONF_FIRMWARE], entry.data[CONF_PREFIX])
    for subentry in entry.subentries.values():
        if subentry.subentry_type in MANAGED_SUBENTRY_TYPES:
            topic = subentry_topic(driver, subentry)
            await _async_remove_from_clock(hass, topic, driver.remove_payload)


async def _async_update_listener(
    hass: HomeAssistant, entry: AwtrixStudioConfigEntry
) -> None:
    """Apply app additions, edits and deletions, or a new loop order."""
    manager = entry.runtime_data
    if (
        manager.prefix == entry.data[CONF_PREFIX]
        and manager.driver.firmware == entry.data[CONF_FIRMWARE]
        and manager.built_from(entry.subentries.values())
    ):
        # Only the clock's options changed: no need to reload the apps.
        manager.set_order(entry.options.get(CONF_APP_ORDER))
        return
    for subentry_id in _removed_app_ids(manager, entry):
        app = manager.apps[subentry_id]
        # The app's switch may already be gone (and the app stopped) by now,
        # so remove the app from the clock explicitly.
        await app.async_disable(remove=False)
        await app.async_remove_from_clock()
    # Several changes in a row (an import) call this once each: only the last
    # call reloads the clock.
    token = object()
    _PENDING_RELOADS[entry.entry_id] = token
    await asyncio.sleep(0)
    if _PENDING_RELOADS.get(entry.entry_id) is not token:
        return
    del _PENDING_RELOADS[entry.entry_id]
    await hass.config_entries.async_reload(entry.entry_id)


@callback
def _removed_app_ids(
    manager: ClockManager, entry: AwtrixStudioConfigEntry
) -> list[str]:
    return [sid for sid in manager.apps if sid not in entry.subentries]


async def _async_remove_from_clock(
    hass: HomeAssistant, topic: str, payload: str
) -> None:
    try:
        await mqtt.async_publish(hass, topic, payload, qos=0, retain=False)
    except HomeAssistantError as err:
        _LOGGER.debug("Could not remove app on %s: %s", topic, err)
