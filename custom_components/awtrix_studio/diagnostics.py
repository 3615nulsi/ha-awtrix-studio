"""Diagnostics of a clock, for bug reports.

What is shared: the firmware and its version, the MQTT prefix, whether the
clock's address and web page credentials are known (never their value), the
display, how many effects, overlays and palettes the clock offers, the app
loop, and for each app or indicator its state and its last errors.

What is not: templates and the pages they render. They often name entities,
places or people and may show any state of the house, so a template is only
described by its length, and simple-mode settings by their field names. Error
messages are kept (they are what a bug report needs) but shortened: a
template error can quote a piece of the rendered text.
"""

from __future__ import annotations

from typing import Any, Final

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from . import AwtrixStudioConfigEntry
from .const import (
    CONF_APP_NAME,
    CONF_APP_ORDER,
    CONF_CYCLE_ONLY_ON_SCREEN,
    CONF_FIRMWARE,
    CONF_HIDE_WHEN_EMPTY,
    CONF_INDICATOR,
    CONF_LIFETIME,
    CONF_MODE,
    CONF_PAGE_INTERVAL,
    CONF_PREFIX,
    CONF_TEMPLATE,
)
from .loop import clean_order
from .manager import MANAGED_SUBENTRY_TYPES, ClockManager

# Never in a report: the clock's address and its web page credentials.
TO_REDACT: Final = {CONF_USERNAME, CONF_PASSWORD, "address", "ip_address"}
# Settings shared as they are: they hold no private data.
SHARED_SETTINGS: Final = (
    CONF_APP_NAME,
    CONF_INDICATOR,
    CONF_MODE,
    CONF_PAGE_INTERVAL,
    CONF_CYCLE_ONLY_ON_SCREEN,
    CONF_HIDE_WHEN_EMPTY,
    CONF_LIFETIME,
)
# Longest piece of an error message kept.
MAX_ERROR: Final = 200


def _short(text: str | None) -> str | None:
    if text is None or len(text) <= MAX_ERROR:
        return text
    return f"{text[:MAX_ERROR]}…"


def _clock(manager: ClockManager) -> dict[str, Any]:
    version = manager.driver.version
    display = manager.display
    capabilities = manager.capabilities
    return {
        "version": ".".join(map(str, version)) if version is not None else None,
        "online": manager.online,
        "address_known": manager.files.address is not None,
        "web_credentials": manager.files.has_credentials,
        "display": {
            "width": display.width,
            "height": display.height,
            "scale": display.scale,
        },
        "capabilities": (
            {key: len(names) for key, names in capabilities.items()}
            if capabilities is not None
            else None
        ),
        "current_app": manager.current_app,
        "loop": manager.loop,
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: AwtrixStudioConfigEntry
) -> dict[str, Any]:
    """Return the diagnostics of a clock."""
    manager: ClockManager | None = (
        entry.runtime_data if entry.state is ConfigEntryState.LOADED else None
    )
    items: list[dict[str, Any]] = []
    for subentry_id, subentry in entry.subentries.items():
        if subentry.subentry_type not in MANAGED_SUBENTRY_TYPES:
            continue
        data = subentry.data
        app = manager.apps.get(subentry_id) if manager is not None else None
        item: dict[str, Any] = {
            "kind": subentry.subentry_type,
            **{key: data[key] for key in SHARED_SETTINGS if key in data},
            "fields": sorted(key for key in data if key not in SHARED_SETTINGS),
        }
        if isinstance(template := data.get(CONF_TEMPLATE), str):
            item["template_length"] = len(template)
        if app is not None:
            item |= {
                "enabled": app.enabled,
                "on_screen": app.on_screen,
                "page_count": app.page_count,
                "template_error": _short(app.last_error),
                "clock_error": _short(app.clock_error),
            }
        items.append(item)
    return {
        "entry": {
            "state": entry.state.value,
            "data": async_redact_data(dict(entry.data), TO_REDACT),
        },
        "firmware": entry.data[CONF_FIRMWARE],
        "prefix": entry.data[CONF_PREFIX],
        "order": clean_order(entry.options.get(CONF_APP_ORDER)),
        "clock": _clock(manager) if manager is not None else None,
        "items": items,
    }
