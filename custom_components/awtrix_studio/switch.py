"""One switch per live app: turning it off removes the app from the clock."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigSubentry
from homeassistant.const import STATE_OFF
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import AwtrixStudioConfigEntry
from .const import (
    CONF_INDICATOR,
    CONF_PREFIX,
    DOMAIN,
    SUBENTRY_TYPE_INDICATOR,
    SUBENTRY_TYPE_NOTIFICATION,
    signal_new_items,
)
from .live_app import LiveApp
from .manager import MANAGED_SUBENTRY_TYPES
from .notifications import NotificationRule

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AwtrixStudioConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a switch for every app, indicator and notification of the clock.

    The switches share the clock's device, so they belong to the entry itself:
    a device can only belong to one subentry. Items added later, without
    reloading the clock, get their switch through a signal.
    """
    manager = entry.runtime_data

    @callback
    def _add(subentry_ids: list[str]) -> None:
        switches = []
        for subentry_id in subentry_ids:
            subentry = entry.subentries.get(subentry_id)
            if subentry is None:
                continue
            if subentry.subentry_type == SUBENTRY_TYPE_NOTIFICATION:
                item = manager.notifications.get(subentry_id)
            elif subentry.subentry_type in MANAGED_SUBENTRY_TYPES:
                item = manager.apps.get(subentry_id)
            else:
                continue
            if item is not None:
                switches.append(AppSwitch(entry, subentry, item))
        if switches:
            async_add_entities(switches)

    _add(list(entry.subentries))
    entry.async_on_unload(
        async_dispatcher_connect(hass, signal_new_items(entry.entry_id), _add)
    )


class AppSwitch(SwitchEntity, RestoreEntity):
    """Enable or disable a live app or indicator."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        entry: AwtrixStudioConfigEntry,
        subentry: ConfigSubentry,
        app: LiveApp | NotificationRule,
    ) -> None:
        """Initialize the switch."""
        self._app = app
        self._attr_unique_id = f"{subentry.subentry_id}_enabled"
        if subentry.subentry_type == SUBENTRY_TYPE_INDICATOR:
            self._attr_translation_key = "indicator"
            self._attr_translation_placeholders = {
                "number": subentry.data[CONF_INDICATOR]
            }
        elif subentry.subentry_type == SUBENTRY_TYPE_NOTIFICATION:
            self._attr_translation_key = "notification"
            self._attr_translation_placeholders = {"name": app.name}
        else:
            self._attr_translation_key = "app"
            self._attr_translation_placeholders = {"app_name": app.name}
        prefix = entry.data[CONF_PREFIX]
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"{prefix} Studio",
            manufacturer="Awtrix Studio",
            model="AWTRIX clock",
        )

    @property
    def is_on(self) -> bool:
        """Return True if the app is running."""
        return self._app.enabled

    async def async_added_to_hass(self) -> None:
        """Restore the previous state and start the app accordingly."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is None or last_state.state != STATE_OFF:
            await self._app.async_enable()
        else:
            # Make sure a previously disabled app is not left on the clock.
            await self._app.async_remove_from_clock()

    async def async_will_remove_from_hass(self) -> None:
        """Stop the app when the entity goes away.

        On a plain unload (restart, reload) the app stays on the clock until it
        is published again. If the user disabled the entity, the app is removed.
        """
        disabled = self.registry_entry is not None and self.registry_entry.disabled
        await self._app.async_disable(remove=disabled)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Start the app."""
        await self._app.async_enable()
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Stop the app and remove it from the clock (or turn the indicator off)."""
        await self._app.async_disable(remove=True)
        self.async_write_ha_state()
