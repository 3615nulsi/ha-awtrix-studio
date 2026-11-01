"""Export and import apps and indicators, to share them or copy them between clocks.

The file is JSON:

    {
      "awtrix_studio": 1,
      "items": [
        {"type": "app", "mode": "simple", "app_name": "co2", ...},
        {"type": "app", "mode": "template", "firmware": "awtrix3", ...},
        {"type": "indicator", "mode": "simple", "indicator": "1", ...}
      ]
    }

Each item holds the same settings as the stored configuration. Simple-mode
items describe *what* to show (an entity, colors, a gradient) and do not
depend on the firmware: they will move to another firmware as they are.
Template-mode items contain the firmware's native JSON, so they carry the
firmware they were written for, and importing them on another firmware is
flagged.

An imported file is untrusted: every item goes through the same validation as
the panel and the config flows, and only whitelisted settings are kept.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import re
from typing import Any, Final

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, valid_entity_id
from homeassistant.exceptions import TemplateError
from homeassistant.helpers.template import Template

from .const import (
    APP_NAME_PATTERN,
    CONF_APP_NAME,
    CONF_ENTITY_ID,
    CONF_INDICATOR,
    CONF_LAYOUT,
    CONF_MODE,
    CONF_TEMPLATE,
    CONF_TEXT_FONT,
    CONF_VALUE_FONT,
    FIRMWARE_AWTRIX_NG,
    INDICATORS,
    MODE_SIMPLE,
    MODE_TEMPLATE,
    SUBENTRY_TYPE_APP,
    SUBENTRY_TYPE_INDICATOR,
)
from .templates import app_segments
from .validation import Invalid, normalize

FORMAT_KEY: Final = "awtrix_studio"
FORMAT_VERSION: Final = 1
MAX_ITEMS: Final = 64

ACTION_ADD: Final = "add"
ACTION_REPLACE: Final = "replace"
ACTION_SKIP: Final = "skip"
ACTIONS: Final = (ACTION_ADD, ACTION_REPLACE, ACTION_SKIP)

KINDS: Final = (SUBENTRY_TYPE_APP, SUBENTRY_TYPE_INDICATOR)


class InvalidDocumentError(ValueError):
    """A file that is not an Awtrix Studio export."""

    def __init__(self, key: str) -> None:
        """Remember the error key to show."""
        super().__init__(key)
        self.key = key


# ------------------------------------------------------------------ export


def export_document(
    entry: ConfigEntry, firmware: str, subentry_ids: list[str] | None = None
) -> dict[str, Any]:
    """Build the export of some (or all) apps and indicators of a clock."""
    items: list[dict[str, Any]] = []
    for subentry_id, subentry in entry.subentries.items():
        if subentry.subentry_type not in KINDS:
            continue
        if subentry_ids is not None and subentry_id not in subentry_ids:
            continue
        item: dict[str, Any] = {"type": subentry.subentry_type, **subentry.data}
        if item.get(CONF_MODE) == MODE_TEMPLATE:
            item["firmware"] = firmware
        items.append(item)
    return {FORMAT_KEY: FORMAT_VERSION, "items": items}


# ------------------------------------------------------------------ import


@dataclass
class ImportItem:
    """One item of a file, as it would be imported."""

    index: int
    kind: str | None
    mode: str | None
    name: str
    action: str
    target: str | None = None
    replaces: str | None = None
    data: dict[str, Any] | None = None
    error: str | None = None
    detail: str = ""
    warnings: list[str] = field(default_factory=list)
    missing_entities: list[str] = field(default_factory=list)
    template: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """What the panel needs to show the item."""
        return {
            "index": self.index,
            "kind": self.kind,
            "mode": self.mode,
            "name": self.name,
            "action": self.action,
            "target": self.target,
            "replaces": self.replaces is not None,
            "error": self.error,
            "detail": self.detail,
            "warnings": self.warnings,
            "missing_entities": self.missing_entities,
            "template": self.template,
        }


def parse_document(document: Any) -> list[Mapping[str, Any]]:
    """Return the raw items of an export, or raise InvalidDocumentError."""
    if not isinstance(document, Mapping) or FORMAT_KEY not in document:
        raise InvalidDocumentError("not_an_export")
    version = document[FORMAT_KEY]
    if not isinstance(version, int) or isinstance(version, bool):
        raise InvalidDocumentError("not_an_export")
    if version > FORMAT_VERSION:
        raise InvalidDocumentError("newer_version")
    items = document.get("items")
    if not isinstance(items, list) or not items:
        raise InvalidDocumentError("no_items")
    if len(items) > MAX_ITEMS:
        raise InvalidDocumentError("too_many_items")
    return [item if isinstance(item, Mapping) else {} for item in items]


def _free_name(name: str, taken: set[str]) -> str:
    base = name[:28] or "app"
    for number in range(2, 1000):
        candidate = f"{base}_{number}"
        if candidate not in taken:
            return candidate
    return base  # pragma: no cover - a thousand apps on one clock


def _entities(hass: HomeAssistant, data: Mapping[str, Any]) -> list[str]:
    """Entities an item uses that do not exist here."""
    used: set[str] = set()
    if data.get(CONF_MODE) == MODE_SIMPLE:
        used |= {segment[CONF_ENTITY_ID] for segment in app_segments(data)}
    else:
        try:
            info = Template(data[CONF_TEMPLATE], hass).async_render_to_info()
        except TemplateError:
            return []
        used |= info.entities
    return sorted(
        entity_id
        for entity_id in used
        if valid_entity_id(entity_id) and hass.states.get(entity_id) is None
    )


def plan_import(  # noqa: PLR0912, PLR0915
    hass: HomeAssistant,
    entry: ConfigEntry,
    firmware: str,
    raw_items: list[Mapping[str, Any]],
    choices: list[Mapping[str, Any]] | None = None,
) -> list[ImportItem]:
    """Decide what to do with each item, validating it for this clock.

    Without choices, each item gets a sensible default: added under its name,
    or under a free name (or a free indicator) when it is taken. Choices may
    skip an item, replace the existing one, or pick another name or indicator.
    """
    existing_apps = {
        subentry.data.get(CONF_APP_NAME): sid
        for sid, subentry in entry.subentries.items()
        if subentry.subentry_type == SUBENTRY_TYPE_APP
    }
    existing_indicators = {
        subentry.data.get(CONF_INDICATOR): sid
        for sid, subentry in entry.subentries.items()
        if subentry.subentry_type == SUBENTRY_TYPE_INDICATOR
    }
    claimed_names = set(existing_apps)
    claimed_indicators: set[str] = set()
    replaced: set[str] = set()
    plans: list[ImportItem] = []

    for index, raw in enumerate(raw_items):
        choice = choices[index] if choices and index < len(choices) else {}
        kind = raw.get("type")
        mode = raw.get(CONF_MODE)
        is_app = kind == SUBENTRY_TYPE_APP
        raw_name = raw.get(CONF_APP_NAME if is_app else CONF_INDICATOR)
        name = str(raw_name)[:64] if isinstance(raw_name, (str, int)) else ""
        plan = ImportItem(
            index=index,
            kind=kind if kind in KINDS else None,
            mode=mode if mode in (MODE_SIMPLE, MODE_TEMPLATE) else None,
            name=name,
            action=ACTION_ADD,
        )
        plans.append(plan)
        if plan.kind is None:
            plan.action, plan.error = ACTION_SKIP, "unknown_type"
            continue
        if mode == MODE_TEMPLATE:
            template = raw.get(CONF_TEMPLATE)
            plan.template = template if isinstance(template, str) else None
            if raw.get("firmware") != firmware:
                plan.warnings.append("other_firmware")

        action = choice.get("action")
        if action not in ACTIONS:
            action = None
        if action == ACTION_SKIP:
            plan.action = ACTION_SKIP
            continue

        # Where the item goes: a name for an app, a number for an indicator.
        requested = choice.get("target")
        if is_app:
            target = requested if isinstance(requested, str) else name
            taken = existing_apps.get(target)
            if action is None:
                if target in claimed_names or not re.fullmatch(
                    APP_NAME_PATTERN, target or ""
                ):
                    clean = re.sub(r"[^A-Za-z0-9_-]", "_", target or "app")[:32]
                    target = (
                        clean
                        if clean not in claimed_names and clean
                        else _free_name(clean, claimed_names)
                    )
                    taken = None
                action = ACTION_ADD
            if action == ACTION_REPLACE and taken is None:
                action = ACTION_ADD
            if action == ACTION_ADD and target in claimed_names:
                plan.action, plan.target = ACTION_SKIP, target
                plan.error = "app_name_exists"
                continue
            claimed_names.add(target)
        else:
            target = requested if requested in INDICATORS else name
            if target not in INDICATORS:
                plan.action, plan.error = ACTION_SKIP, "invalid_value"
                continue
            if action is None:
                if target in existing_indicators or target in claimed_indicators:
                    free = [
                        n
                        for n in INDICATORS
                        if n not in existing_indicators and n not in claimed_indicators
                    ]
                    if free:
                        target = free[0]
                        action = ACTION_ADD
                    else:
                        action = ACTION_REPLACE
                else:
                    action = ACTION_ADD
            if target in claimed_indicators:
                plan.action, plan.target = ACTION_SKIP, target
                plan.error = "indicator_in_use"
                continue
            taken = existing_indicators.get(target)
            if action == ACTION_REPLACE and taken is None:
                action = ACTION_ADD
            if action == ACTION_ADD and taken is not None:
                plan.action, plan.target = ACTION_SKIP, target
                plan.error = "indicator_in_use"
                continue
            claimed_indicators.add(target)

        plan.action, plan.target = action, target
        plan.replaces = taken if action == ACTION_REPLACE else None
        if plan.replaces is not None:
            if plan.replaces in replaced:
                plan.action = ACTION_SKIP
                plan.error = "app_name_exists" if is_app else "indicator_in_use"
                continue
            replaced.add(plan.replaces)

        data = dict(raw)
        data.pop("type", None)
        data.pop("firmware", None)
        if data.get(CONF_LAYOUT) and firmware != FIRMWARE_AWTRIX_NG:
            # Layouts are for AWTRIX NG on a TC002: elsewhere, one line.
            for key in (CONF_LAYOUT, CONF_VALUE_FONT, CONF_TEXT_FONT):
                data.pop(key, None)
            plan.warnings.append("layout_dropped")
        if is_app:
            data[CONF_APP_NAME] = target
        else:
            data[CONF_INDICATOR] = target
        try:
            # Replacing keeps the existing item's identity; a new item is
            # checked against the clock (its name was reserved above).
            plan.data = normalize(hass, entry, plan.kind, data, plan.replaces)
        except Invalid as err:
            plan.action, plan.error, plan.detail = ACTION_SKIP, err.key, err.detail
            continue
        plan.missing_entities = _entities(hass, plan.data)
    return plans
