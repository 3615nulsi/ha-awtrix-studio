"""WebSocket API used by the sidebar panel.

Every command is admin-only and has a strict schema. Values that reach a
template, a topic or the stored configuration go through `validation`.
Nothing here fires events or writes states, so watching the panel leaves the
recorder untouched.
"""

from __future__ import annotations

import json
from types import MappingProxyType
from typing import Any

try:  # Home Assistant 2026.x replaced voluptuous with its fork, probatio.
    import probatio as vol
except ImportError:  # pragma: no cover - older Home Assistant
    import voluptuous as vol

import aiohttp
from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntry, ConfigEntryState, ConfigSubentry
from homeassistant.const import SERVICE_TURN_OFF, SERVICE_TURN_ON
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.exceptions import TemplateError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.event import (
    TrackTemplate,
    TrackTemplateResult,
    async_call_later,
    async_track_template_result,
)
from homeassistant.helpers.template import Template

from .clock_files import ICON_NAME_PATTERN
from .const import (
    APP_NAME_PATTERN,
    CONF_APP_NAME,
    CONF_APP_ORDER,
    CONF_FIRMWARE,
    CONF_INDICATOR,
    CONF_MODE,
    CONF_PREFIX,
    CONF_TEMPLATE,
    DATA_ICONS,
    DOMAIN,
    MODE_SIMPLE,
    SUBENTRY_TYPE_APP,
    SUBENTRY_TYPE_INDICATOR,
)
from .display import CONF_DISPLAY, apply_display
from .drivers import Screen
from .icons import IconError, IconStore, convert, large_pixels
from .loop import MAX_APP_NAME, MAX_LOOP, clean_order
from .manager import MANAGED_SUBENTRY_TYPES, ClockManager
from .pages import InvalidPagesError, parse_pages, serialize_page
from .templates import (
    InvalidSimpleOptionsError,
    build_app_template,
    build_indicator_template,
)
from .transfer import (
    ACTION_ADD,
    ACTION_REPLACE,
    MAX_ITEMS,
    InvalidDocumentError,
    export_document,
    parse_document,
    plan_import,
)
from .validation import Invalid, normalize

KIND = vol.In(MANAGED_SUBENTRY_TYPES)
ENTRY_ID = vol.All(str, vol.Length(min=1, max=64))
SUBENTRY_ID = vol.All(str, vol.Length(min=1, max=64))
DATA = vol.All(dict, vol.Length(max=48))
SNAPSHOT_COALESCE = 0.1


def signal_entry_loaded(entry_id: str) -> str:
    """Dispatcher signal sent when a clock's entry has (re)loaded."""
    return f"{DOMAIN}_loaded_{entry_id}"


@callback
def async_setup(hass: HomeAssistant) -> None:
    """Register the panel's commands."""
    for command in (
        ws_clocks,
        ws_subscribe,
        ws_screen,
        ws_preview,
        ws_save,
        ws_delete,
        ws_enable,
        ws_test,
        ws_show,
        ws_navigate,
        ws_republish,
        ws_loop,
        ws_order,
        ws_export,
        ws_import,
        ws_icon_search,
        ws_icon_get,
        ws_icon_clock,
    ):
        websocket_api.async_register_command(hass, command)


# ---------------------------------------------------------------- helpers


def _entry(hass: HomeAssistant, entry_id: str) -> ConfigEntry | None:
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        return None
    return entry


def _manager(entry: ConfigEntry) -> ClockManager | None:
    if entry.state is not ConfigEntryState.LOADED:
        return None
    return entry.runtime_data


def _loads(page: str) -> Any:
    return json.loads(page)


def _snapshot(entry: ConfigEntry, manager: ClockManager | None) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for subentry_id, subentry in entry.subentries.items():
        if subentry.subentry_type not in MANAGED_SUBENTRY_TYPES:
            continue
        app = manager.apps.get(subentry_id) if manager else None
        items.append(
            {
                "subentry_id": subentry_id,
                "kind": subentry.subentry_type,
                "title": subentry.title,
                "data": dict(subentry.data),
                "enabled": bool(app and app.enabled),
                "on_screen": bool(app and app.on_screen),
                "pages": [_loads(p) for p in app.pages] if app else [],
                "page_index": app.page_index if app else 0,
                "error": app.last_error if app else None,
                "clock_error": app.clock_error if app else None,
            }
        )
    driver = manager.driver if manager else None
    return {
        "entry_id": entry.entry_id,
        "title": entry.title,
        "prefix": entry.data[CONF_PREFIX],
        "firmware": entry.data[CONF_FIRMWARE],
        "loaded": manager is not None,
        "current_app": manager.current_app if manager else None,
        "order": clean_order(entry.options.get(CONF_APP_ORDER)),
        "loop": manager.loop if manager else None,
        "capabilities": manager.capabilities if manager else None,
        "display": (
            {
                "width": manager.display.width,
                "height": manager.display.height,
                "scale": manager.display.scale,
            }
            if manager
            else {"width": 32, "height": 8, "scale": 1}
        ),
        "features": {
            "screen": bool(driver and driver.screen_topic),
            "navigate": bool(driver and driver.navigate(True)),
            "order": bool(driver and driver.can_reorder),
            "icon_files": bool(driver and driver.icon_file("") is not None),
        },
        "items": items,
    }


def _build_template(entry: ConfigEntry, kind: str, data: dict[str, Any]) -> str:
    if data[CONF_MODE] != MODE_SIMPLE:
        return data[CONF_TEMPLATE]
    firmware = entry.data[CONF_FIRMWARE]
    if kind == SUBENTRY_TYPE_INDICATOR:
        return build_indicator_template(data, firmware)
    return build_app_template(data, firmware)


def _add_item(
    hass: HomeAssistant, entry: ConfigEntry, kind: str, data: dict[str, Any]
) -> ConfigSubentry:
    """Store a new, validated app or indicator."""
    if kind == SUBENTRY_TYPE_INDICATOR:
        title = f"Indicator {data[CONF_INDICATOR]}"
        unique_id = f"indicator:{data[CONF_INDICATOR]}"
    else:
        title = unique_id = data[CONF_APP_NAME]
    subentry = ConfigSubentry(
        data=MappingProxyType(data),
        subentry_type=kind,
        title=title,
        unique_id=unique_id,
    )
    hass.config_entries.async_add_subentry(entry, subentry)
    return subentry


def _error(
    connection: websocket_api.ActiveConnection, msg: dict, code: str, text: str
) -> None:
    connection.send_error(msg["id"], code, text)


# --------------------------------------------------------------- commands


@websocket_api.websocket_command({vol.Required("type"): "awtrix_studio/clocks"})
@websocket_api.require_admin
@callback
def ws_clocks(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """List the configured clocks."""
    connection.send_result(
        msg["id"],
        [
            {
                "entry_id": entry.entry_id,
                "title": entry.title,
                "prefix": entry.data[CONF_PREFIX],
                "firmware": entry.data[CONF_FIRMWARE],
                "loaded": entry.state is ConfigEntryState.LOADED,
            }
            for entry in hass.config_entries.async_entries(DOMAIN)
        ],
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): "awtrix_studio/subscribe",
        vol.Required("entry_id"): ENTRY_ID,
    }
)
@websocket_api.require_admin
@callback
def ws_subscribe(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Stream a clock's apps and indicators as they change."""
    entry = _entry(hass, msg["entry_id"])
    if entry is None:
        _error(connection, msg, websocket_api.ERR_NOT_FOUND, "Unknown clock")
        return

    pending: CALLBACK_TYPE | None = None
    detach: CALLBACK_TYPE | None = None

    @callback
    def send(_now: Any = None) -> None:
        nonlocal pending
        pending = None
        current = _entry(hass, msg["entry_id"])
        if current is not None:
            connection.send_message(
                websocket_api.event_message(
                    msg["id"], _snapshot(current, _manager(current))
                )
            )

    @callback
    def schedule() -> None:
        nonlocal pending
        if pending is None:
            pending = async_call_later(hass, SNAPSHOT_COALESCE, send)

    @callback
    def attach() -> None:
        nonlocal detach
        if detach is not None:
            detach()
            detach = None
        current = _entry(hass, msg["entry_id"])
        manager = _manager(current) if current else None
        if manager is not None:
            detach = manager.async_add_listener(schedule)
        schedule()

    unsub_loaded = async_dispatcher_connect(
        hass, signal_entry_loaded(entry.entry_id), attach
    )

    @callback
    def unsubscribe() -> None:
        unsub_loaded()
        if detach is not None:
            detach()
        if pending is not None:
            pending()

    connection.subscriptions[msg["id"]] = unsubscribe
    connection.send_result(msg["id"])
    attach()


@websocket_api.websocket_command(
    {
        vol.Required("type"): "awtrix_studio/screen",
        vol.Required("entry_id"): ENTRY_ID,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_screen(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Mirror the clock's real screen while subscribed."""
    entry = _entry(hass, msg["entry_id"])
    manager = _manager(entry) if entry else None
    if manager is None or manager.driver.screen_topic is None:
        _error(connection, msg, websocket_api.ERR_NOT_FOUND, "Screen not available")
        return

    @callback
    def forward(screen: Screen) -> None:
        connection.send_message(
            websocket_api.event_message(
                msg["id"],
                {
                    "width": screen.width,
                    "height": screen.height,
                    "pixels": screen.pixels,
                },
            )
        )

    connection.subscriptions[msg["id"]] = await manager.async_subscribe_screen(forward)
    connection.send_result(msg["id"])


@websocket_api.websocket_command(
    {
        vol.Required("type"): "awtrix_studio/preview",
        vol.Required("entry_id"): ENTRY_ID,
        vol.Required("kind"): KIND,
        vol.Optional("subentry_id"): SUBENTRY_ID,
        vol.Required("data"): DATA,
    }
)
@websocket_api.require_admin
@callback
def ws_preview(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Render an app or indicator being edited, live, without saving it."""
    entry = _entry(hass, msg["entry_id"])
    if entry is None:
        _error(connection, msg, websocket_api.ERR_NOT_FOUND, "Unknown clock")
        return
    kind, subentry_id = msg["kind"], msg.get("subentry_id")
    if subentry_id is not None and subentry_id not in entry.subentries:
        _error(connection, msg, websocket_api.ERR_NOT_FOUND, "Unknown item")
        return

    def send(payload: dict[str, Any]) -> None:
        connection.send_message(websocket_api.event_message(msg["id"], payload))

    connection.send_result(msg["id"])
    try:
        data = normalize(hass, entry, kind, msg["data"], subentry_id, preview=True)
        template_str = _build_template(entry, kind, data)
        template = Template(template_str, hass)
        template.ensure_valid()
    except Invalid as err:
        send({"pages": [], "error": err.key, "detail": err.detail, "field": err.field})
        connection.subscriptions[msg["id"]] = lambda: None
        return
    except (InvalidSimpleOptionsError, TemplateError) as err:
        send({"pages": [], "error": "invalid_template", "detail": str(err)})
        connection.subscriptions[msg["id"]] = lambda: None
        return

    generated = template_str if data[CONF_MODE] == MODE_SIMPLE else None

    @callback
    def on_result(event: Event[Any] | None, updates: list[TrackTemplateResult]) -> None:
        result = updates[-1].result
        if isinstance(result, TemplateError):
            send(
                {
                    "pages": [],
                    "error": "invalid_template",
                    "detail": str(result),
                    "template": generated,
                }
            )
            return
        try:
            pages = parse_pages(result)
        except InvalidPagesError as err:
            send(
                {
                    "pages": [],
                    "error": "invalid_pages",
                    "detail": str(err),
                    "template": generated,
                }
            )
            return
        if kind == SUBENTRY_TYPE_INDICATOR:
            pages = pages[:1]
        else:
            pages = apply_display(
                pages, data.get(CONF_DISPLAY), entry.data[CONF_FIRMWARE]
            )
        send({"pages": pages, "error": None, "template": generated})

    tracker = async_track_template_result(
        hass, [TrackTemplate(template, None)], on_result, log_fn=lambda *_: None
    )
    tracker.async_refresh()
    connection.subscriptions[msg["id"]] = tracker.async_remove


@websocket_api.websocket_command(
    {
        vol.Required("type"): "awtrix_studio/save",
        vol.Required("entry_id"): ENTRY_ID,
        vol.Required("kind"): KIND,
        vol.Optional("subentry_id"): SUBENTRY_ID,
        vol.Required("data"): DATA,
    }
)
@websocket_api.require_admin
@callback
def ws_save(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Create or update an app or indicator."""
    entry = _entry(hass, msg["entry_id"])
    if entry is None:
        _error(connection, msg, websocket_api.ERR_NOT_FOUND, "Unknown clock")
        return
    kind, subentry_id = msg["kind"], msg.get("subentry_id")
    if subentry_id is not None and (
        subentry_id not in entry.subentries
        or entry.subentries[subentry_id].subentry_type != kind
    ):
        _error(connection, msg, websocket_api.ERR_NOT_FOUND, "Unknown item")
        return
    try:
        data = normalize(hass, entry, kind, msg["data"], subentry_id)
    except Invalid as err:
        connection.send_result(
            msg["id"], {"errors": {err.field: err.key}, "detail": err.detail}
        )
        return

    if subentry_id is not None:
        hass.config_entries.async_update_subentry(
            entry, entry.subentries[subentry_id], data=data
        )
        connection.send_result(msg["id"], {"subentry_id": subentry_id})
        return

    subentry = _add_item(hass, entry, kind, data)
    connection.send_result(msg["id"], {"subentry_id": subentry.subentry_id})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "awtrix_studio/delete",
        vol.Required("entry_id"): ENTRY_ID,
        vol.Required("subentry_id"): SUBENTRY_ID,
    }
)
@websocket_api.require_admin
@callback
def ws_delete(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Delete an app or indicator (it is removed from the clock too)."""
    entry = _entry(hass, msg["entry_id"])
    if entry is None or msg["subentry_id"] not in entry.subentries:
        _error(connection, msg, websocket_api.ERR_NOT_FOUND, "Unknown item")
        return
    hass.config_entries.async_remove_subentry(entry, msg["subentry_id"])
    connection.send_result(msg["id"])


@websocket_api.websocket_command(
    {
        vol.Required("type"): "awtrix_studio/enable",
        vol.Required("entry_id"): ENTRY_ID,
        vol.Required("subentry_id"): SUBENTRY_ID,
        vol.Required("enabled"): bool,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_enable(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Turn an app or indicator on or off, through its switch."""
    entity_id = er.async_get(hass).async_get_entity_id(
        "switch", DOMAIN, f"{msg['subentry_id']}_enabled"
    )
    if entity_id is None or hass.states.get(entity_id) is None:
        _error(connection, msg, websocket_api.ERR_NOT_FOUND, "Switch not available")
        return
    await hass.services.async_call(
        "switch",
        SERVICE_TURN_ON if msg["enabled"] else SERVICE_TURN_OFF,
        {"entity_id": entity_id},
        blocking=True,
        context=connection.context(msg),
    )
    connection.send_result(msg["id"])


@websocket_api.websocket_command(
    {
        vol.Required("type"): "awtrix_studio/test",
        vol.Required("entry_id"): ENTRY_ID,
        vol.Required("kind"): KIND,
        vol.Optional("subentry_id"): SUBENTRY_ID,
        vol.Required("data"): DATA,
        vol.Optional("page", default=0): vol.All(int, vol.Range(min=0, max=50)),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_test(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Show an unsaved app or indicator on the clock for a few seconds."""
    entry = _entry(hass, msg["entry_id"])
    manager = _manager(entry) if entry else None
    if entry is None or manager is None:
        _error(connection, msg, websocket_api.ERR_NOT_FOUND, "Unknown clock")
        return
    kind, subentry_id = msg["kind"], msg.get("subentry_id")
    if subentry_id is not None and subentry_id not in entry.subentries:
        _error(connection, msg, websocket_api.ERR_NOT_FOUND, "Unknown item")
        return
    try:
        data = normalize(hass, entry, kind, msg["data"], subentry_id)
        pages = parse_pages(
            Template(_build_template(entry, kind, data), hass).async_render()
        )
        pages = apply_display(pages, data.get(CONF_DISPLAY), entry.data[CONF_FIRMWARE])
    except Invalid as err:
        connection.send_result(
            msg["id"], {"errors": {err.field: err.key}, "detail": err.detail}
        )
        return
    except (InvalidSimpleOptionsError, TemplateError, InvalidPagesError) as err:
        connection.send_result(
            msg["id"],
            {"errors": {CONF_TEMPLATE: "invalid_template"}, "detail": str(err)},
        )
        return

    if kind == SUBENTRY_TYPE_INDICATOR:
        topic = manager.driver.indicator_topic(data[CONF_INDICATOR])
    else:
        topic = manager.driver.app_topic(data[CONF_APP_NAME])
    payload = (
        serialize_page(pages[min(msg["page"], len(pages) - 1)])
        if pages
        else manager.driver.remove_payload
    )
    restore = manager.apps.get(subentry_id) if subentry_id else None
    await manager.async_test(topic, payload, restore)
    if kind == SUBENTRY_TYPE_APP and pages:
        await manager.async_send(manager.driver.show_app(data[CONF_APP_NAME]))
    connection.send_result(msg["id"], {"published": bool(pages)})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "awtrix_studio/show",
        vol.Required("entry_id"): ENTRY_ID,
        vol.Required("app_name"): vol.All(str, vol.Match(APP_NAME_PATTERN)),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_show(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Bring an app on screen now."""
    entry = _entry(hass, msg["entry_id"])
    manager = _manager(entry) if entry else None
    if manager is None:
        _error(connection, msg, websocket_api.ERR_NOT_FOUND, "Unknown clock")
        return
    await manager.async_send(manager.driver.show_app(msg["app_name"]))
    connection.send_result(msg["id"])


@websocket_api.websocket_command(
    {
        vol.Required("type"): "awtrix_studio/navigate",
        vol.Required("entry_id"): ENTRY_ID,
        vol.Required("forward"): bool,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_navigate(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Move the clock to the next or previous app."""
    entry = _entry(hass, msg["entry_id"])
    manager = _manager(entry) if entry else None
    if manager is None:
        _error(connection, msg, websocket_api.ERR_NOT_FOUND, "Unknown clock")
        return
    await manager.async_send(manager.driver.navigate(msg["forward"]))
    connection.send_result(msg["id"])


@websocket_api.websocket_command(
    {
        vol.Required("type"): "awtrix_studio/republish",
        vol.Required("entry_id"): ENTRY_ID,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_republish(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Publish every app of a clock again."""
    entry = _entry(hass, msg["entry_id"])
    manager = _manager(entry) if entry else None
    if manager is None:
        _error(connection, msg, websocket_api.ERR_NOT_FOUND, "Unknown clock")
        return
    await manager.async_republish_all()
    connection.send_result(msg["id"])


@websocket_api.websocket_command(
    {
        vol.Required("type"): "awtrix_studio/loop",
        vol.Required("entry_id"): ENTRY_ID,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_loop(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Read the clock's app loop again (None when the clock cannot be reached)."""
    entry = _entry(hass, msg["entry_id"])
    manager = _manager(entry) if entry else None
    if entry is None or manager is None:
        _error(connection, msg, websocket_api.ERR_NOT_FOUND, "Unknown clock")
        return
    connection.send_result(msg["id"], {"loop": await manager.async_refresh_loop()})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "awtrix_studio/order",
        vol.Required("entry_id"): ENTRY_ID,
        vol.Required("order"): vol.All(
            [vol.All(str, vol.Length(min=1, max=MAX_APP_NAME))],
            vol.Length(max=MAX_LOOP),
        ),
    }
)
@websocket_api.require_admin
@callback
def ws_order(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Store the order of the app loop; the clock follows it from then on."""
    entry = _entry(hass, msg["entry_id"])
    if entry is None:
        _error(connection, msg, websocket_api.ERR_NOT_FOUND, "Unknown clock")
        return
    order = clean_order(msg["order"])
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_APP_ORDER: order}
    )
    connection.send_result(msg["id"], {"order": order})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "awtrix_studio/export",
        vol.Required("entry_id"): ENTRY_ID,
        vol.Optional("subentry_ids"): vol.All(
            [SUBENTRY_ID], vol.Length(min=1, max=MAX_ITEMS)
        ),
    }
)
@websocket_api.require_admin
@callback
def ws_export(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Export apps and indicators to share them or copy them to another clock."""
    entry = _entry(hass, msg["entry_id"])
    if entry is None:
        _error(connection, msg, websocket_api.ERR_NOT_FOUND, "Unknown clock")
        return
    connection.send_result(
        msg["id"],
        export_document(entry, entry.data[CONF_FIRMWARE], msg.get("subentry_ids")),
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): "awtrix_studio/import",
        vol.Required("entry_id"): ENTRY_ID,
        vol.Required("document"): vol.All(dict, vol.Length(max=8)),
        vol.Optional("choices"): vol.All(
            [vol.All(dict, vol.Length(max=4))], vol.Length(max=MAX_ITEMS)
        ),
        vol.Optional("dry_run", default=False): bool,
    }
)
@websocket_api.require_admin
@callback
def ws_import(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Check an export (dry run), then import the chosen items."""
    entry = _entry(hass, msg["entry_id"])
    if entry is None:
        _error(connection, msg, websocket_api.ERR_NOT_FOUND, "Unknown clock")
        return
    try:
        raw_items = parse_document(msg["document"])
    except InvalidDocumentError as err:
        connection.send_result(msg["id"], {"error": err.key, "items": []})
        return
    plans = plan_import(
        hass, entry, entry.data[CONF_FIRMWARE], raw_items, msg.get("choices")
    )
    imported = 0
    if not msg["dry_run"]:
        for plan in plans:
            if plan.data is None or plan.kind is None:
                continue
            if plan.action == ACTION_REPLACE and plan.replaces in entry.subentries:
                hass.config_entries.async_update_subentry(
                    entry, entry.subentries[plan.replaces], data=plan.data
                )
                imported += 1
            elif plan.action == ACTION_ADD:
                _add_item(hass, entry, plan.kind, plan.data)
                imported += 1
    connection.send_result(
        msg["id"],
        {"error": None, "imported": imported, "items": [p.as_dict() for p in plans]},
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): "awtrix_studio/icons/search",
        vol.Required("query"): vol.All(str, vol.Length(max=64)),
        vol.Optional("page", default=1): vol.All(int, vol.Range(min=1, max=100)),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_icon_search(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Search the LaMetric icon gallery (through Home Assistant)."""
    icons: IconStore = hass.data[DATA_ICONS]
    try:
        result = await icons.async_search(msg["query"].strip(), msg["page"])
    except IconError as err:
        _error(connection, msg, "icons_unavailable", str(err))
        return
    connection.send_result(msg["id"], result)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "awtrix_studio/icons/get",
        vol.Required("icon_id"): vol.All(int, vol.Range(min=1, max=9_999_999)),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_icon_get(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Download a LaMetric icon (once) and return its pixels for the preview."""
    icons: IconStore = hass.data[DATA_ICONS]
    icon = await icons.async_get(msg["icon_id"])
    if icon is None:
        _error(connection, msg, "icons_unavailable", "Icon not available")
        return
    connection.send_result(
        msg["id"], {"pixels": icon["pixels"], "animated": icon["animated"]}
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): "awtrix_studio/icons/clock",
        vol.Required("entry_id"): ENTRY_ID,
        vol.Required("name"): vol.All(str, vol.Match(ICON_NAME_PATTERN)),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_icon_clock(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Return the pixels of an icon stored on the clock, for the preview.

    `pixels` (8x8) is None when the clock has no such file. A 16x16 icon also
    comes as is, in `large`, for a TC002 that shows it at that size.
    """
    entry = _entry(hass, msg["entry_id"])
    manager = _manager(entry) if entry else None
    if entry is None or manager is None:
        _error(connection, msg, websocket_api.ERR_NOT_FOUND, "Unknown clock")
        return
    try:
        data = await manager.files.async_read_icon(msg["name"])
        icon = large = None
        if data is not None:
            icon = await hass.async_add_executor_job(convert, data)
            large = await hass.async_add_executor_job(large_pixels, data)
    except (aiohttp.ClientError, TimeoutError, ValueError, IconError) as err:
        _error(connection, msg, "icons_unavailable", str(err) or type(err).__name__)
        return
    connection.send_result(
        msg["id"], {"pixels": icon["pixels"] if icon else None, "large": large}
    )
