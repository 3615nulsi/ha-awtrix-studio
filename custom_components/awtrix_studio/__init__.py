"""Awtrix Studio: AWTRIX apps bound to Home Assistant templates."""

from __future__ import annotations

import asyncio
import logging

from homeassistant.components import mqtt
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
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
    CONF_NOTIFICATION_NAME,
    CONF_PREFIX,
    DATA_ICONS,
    DOMAIN,
    SERVICE_REFRESH,
    SUBENTRY_TYPE_NOTIFICATION,
    signal_new_items,
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
# The same, for the changes applied without reloading the clock.
_PENDING_SYNCS: dict[str, object] = {}

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
    manager.files.set_credentials(
        entry.data.get(CONF_USERNAME), entry.data.get(CONF_PASSWORD)
    )
    for subentry in entry.subentries.values():
        if subentry.subentry_type in MANAGED_SUBENTRY_TYPES:
            manager.add_app(subentry)
        elif subentry.subentry_type == SUBENTRY_TYPE_NOTIFICATION:
            manager.add_notification(subentry)
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
        elif (
            subentry.subentry_type == SUBENTRY_TYPE_NOTIFICATION
            and (
                message := driver.dismiss_message(
                    str(subentry.data.get(CONF_NOTIFICATION_NAME, ""))
                )
            )
            is not None
        ):
            await _async_remove_from_clock(hass, *message)


async def _async_update_listener(
    hass: HomeAssistant, entry: AwtrixStudioConfigEntry
) -> None:
    """Apply app additions, edits and deletions, a new loop order, or a new
    prefix, firmware or web page password (the clock was reconfigured).

    Only a new prefix or firmware reloads the clock. Changes to apps,
    indicators and notifications are applied to those alone, so the others
    are not published again and the panel keeps its state.
    """
    manager = entry.runtime_data
    moved = (
        manager.prefix != entry.data[CONF_PREFIX]
        or manager.driver.firmware != entry.data[CONF_FIRMWARE]
    )
    subentries = list(entry.subentries.values())
    if (
        not moved
        and manager.built_from(subentries)
        and manager.notifications_built_from(subentries)
    ):
        # Only the clock's options or credentials changed: no need to reload
        # the apps.
        manager.files.set_credentials(
            entry.data.get(CONF_USERNAME), entry.data.get(CONF_PASSWORD)
        )
        manager.set_order(entry.options.get(CONF_APP_ORDER))
        return
    if not moved:
        await _async_apply_changes(hass, entry)
        return
    # With a new prefix or firmware, every app leaves its old topic: the
    # reload publishes them on the new ones.
    await _async_reload(hass, entry, list(manager.apps))


async def _async_apply_changes(
    hass: HomeAssistant, entry: AwtrixStudioConfigEntry
) -> None:
    """Apply the changed apps and notifications without reloading the clock."""
    # Several changes in a row (an import) call this once each: only the last
    # call applies them all.
    token = object()
    _PENDING_SYNCS[entry.entry_id] = token
    await asyncio.sleep(0)
    if _PENDING_SYNCS.get(entry.entry_id) is not token:
        return
    del _PENDING_SYNCS[entry.entry_id]
    manager = entry.runtime_data
    try:
        added = await manager.async_sync(entry.subentries.values())
        _async_tidy_registries(hass, entry)
        manager.set_order(entry.options.get(CONF_APP_ORDER))
        if added:
            async_dispatcher_send(hass, signal_new_items(entry.entry_id), added)
        manager.async_notify_changed()
    except Exception:
        _LOGGER.exception(
            "Could not apply the changes of %s one by one, reloading it", entry.title
        )
        await _async_reload(hass, entry, _removed_app_ids(manager, entry))


async def _async_reload(
    hass: HomeAssistant, entry: AwtrixStudioConfigEntry, leaving: list[str]
) -> None:
    """Take the apps in `leaving` off the clock, then reload it."""
    manager = entry.runtime_data
    for subentry_id in leaving:
        app = manager.apps.get(subentry_id)
        if app is None:
            continue
        # The app's switch may already be gone (and the app stopped) by now,
        # so remove the app from the clock explicitly.
        await app.async_disable(remove=False)
        await app.async_remove_from_clock()
    # Several changes in a row call this once each: only the last call
    # reloads the clock.
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
