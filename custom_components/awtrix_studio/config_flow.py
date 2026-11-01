"""Config flow: one entry per clock, one subentry per app or indicator."""

from __future__ import annotations

import re
from typing import Any

try:  # Home Assistant 2026.x replaced voluptuous with its fork, probatio.
    import probatio as vol
except ImportError:  # pragma: no cover - older Home Assistant
    import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.core import callback
from homeassistant.exceptions import TemplateError
from homeassistant.helpers.selector import (
    BooleanSelector,
    ColorRGBSelector,
    EntitySelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TemplateSelector,
    TextSelector,
)
from homeassistant.helpers.template import Template

from .const import (
    APP_COLOR_MODES,
    APP_NAME_PATTERN,
    COLOR_GRADIENT,
    COLOR_STEPS,
    CONF_ACTIVE_STATE,
    CONF_APP_NAME,
    CONF_COLOR,
    CONF_COLOR_1,
    CONF_COLOR_2,
    CONF_COLOR_MODE,
    CONF_CYCLE_ONLY_ON_SCREEN,
    CONF_DECIMALS,
    CONF_EFFECT,
    CONF_EFFECT_INTERVAL,
    CONF_ENTITY_ID,
    CONF_FIRMWARE,
    CONF_GRADIENT,
    CONF_GRADIENT_MAX,
    CONF_GRADIENT_MIN,
    CONF_GRADIENT_STOPS,
    CONF_HIDE_WHEN_EMPTY,
    CONF_HIDE_WHEN_ZERO,
    CONF_ICON,
    CONF_INDICATOR,
    CONF_LABEL,
    CONF_LAYOUT,
    CONF_LIFETIME,
    CONF_MODE,
    CONF_PAGE_INTERVAL,
    CONF_PREFIX,
    CONF_PROGRESS,
    CONF_PROGRESS_BG,
    CONF_PROGRESS_COLOR,
    CONF_PROGRESS_ENTITY,
    CONF_PROGRESS_MAX,
    CONF_PROGRESS_MIN,
    CONF_SEGMENTS,
    CONF_SEPARATOR,
    CONF_SHOW_UNIT,
    CONF_TEMPLATE,
    CONF_TEXT_FONT,
    CONF_THRESHOLD_1,
    CONF_THRESHOLD_2,
    CONF_VALUE_FONT,
    DEFAULT_ACTIVE_STATE,
    DEFAULT_COLOR,
    DEFAULT_CYCLE_ONLY_ON_SCREEN,
    DEFAULT_EFFECT_INTERVAL,
    DEFAULT_GRADIENT,
    DEFAULT_HIDE_WHEN_EMPTY,
    DEFAULT_INDICATOR_COLOR,
    DEFAULT_LIFETIME,
    DEFAULT_PAGE_INTERVAL,
    DEFAULT_PREFIX,
    DEFAULT_PROGRESS_BG,
    DEFAULT_PROGRESS_MAX,
    DEFAULT_PROGRESS_MIN,
    DEFAULT_SEPARATOR,
    DOMAIN,
    EFFECT_NONE,
    EFFECTS,
    FIRMWARE_AWTRIX3,
    FIRMWARE_AWTRIX_NG,
    GRADIENT_CUSTOM,
    INDICATOR_COLOR_MODES,
    INDICATORS,
    MAX_LIFETIME,
    MAX_PAGE_INTERVAL,
    MIN_PAGE_INTERVAL,
    MODE_SIMPLE,
    MODE_TEMPLATE,
    SUBENTRY_TYPE_APP,
    SUBENTRY_TYPE_INDICATOR,
)
from .detect import async_detect_firmware
from .display import CONF_DISPLAY
from .drivers import DRIVERS
from .gradients import GRADIENTS, InvalidGradientError, resolve_stops, stops_to_data
from .pages import InvalidPagesError, parse_pages
from .templates import (
    app_color_mode,
    build_app_template,
    build_indicator_template,
    indicator_color_mode,
)

MQTT_DOMAIN = "mqtt"
# Firmware choice that asks the clock which firmware it runs.
FIRMWARE_AUTO = "auto"

STARTER_APP_TEMPLATE = """{#- Render a JSON object (one page) or a list of objects
    (pages that alternate). Any key of the AWTRIX custom app API works. -#}
{{ [
  {"text": now().strftime('%H:%M'), "color": "#FFFFFF"},
  {"text": states('sun.sun'), "color": "#FFB74D"}
] | tojson }}"""

STARTER_INDICATOR_TEMPLATE = """{#- Render {"color": ...} to light the indicator
    (optionally with "blink" or "fade" in ms), or {} to turn it off. -#}
{%- if is_state('sun.sun', 'below_horizon') -%}
{"color": "#FF8C00", "fade": 2000}
{%- else -%}
{}
{%- endif -%}"""


NG_STARTER_APP_TEMPLATE = """{#- Render a JSON object (one page) or a list of objects
    (pages that alternate). Any key of the AWTRIX NG pushed app API works;
    unknown keys make the clock refuse the app. -#}
{{ [
  {"text": now().strftime('%H:%M'), "textColor": "#FFFFFF"},
  {"text": states('sun.sun'), "textColor": "#FFB74D"}
] | tojson }}"""

NG_STARTER_INDICATOR_TEMPLATE = """{#- Render {"color": ...} to light the indicator
    (optionally with "blinkMs" or "fadeMs"), or {} to turn it off. -#}
{%- if is_state('sun.sun', 'below_horizon') -%}
{"color": "#FF8C00", "fadeMs": 2000}
{%- else -%}
{}
{%- endif -%}"""

# The starting template of each kind, in each firmware's own JSON.
STARTER_TEMPLATES: dict[str, dict[str, str]] = {
    FIRMWARE_AWTRIX3: {
        SUBENTRY_TYPE_APP: STARTER_APP_TEMPLATE,
        SUBENTRY_TYPE_INDICATOR: STARTER_INDICATOR_TEMPLATE,
    },
    FIRMWARE_AWTRIX_NG: {
        SUBENTRY_TYPE_APP: NG_STARTER_APP_TEMPLATE,
        SUBENTRY_TYPE_INDICATOR: NG_STARTER_INDICATOR_TEMPLATE,
    },
}


def _normalize_prefix(prefix: str) -> str | None:
    """Return a clean MQTT prefix, or None if it is not usable."""
    prefix = prefix.strip().strip("/")
    if not prefix or any(char in prefix for char in "#+ "):
        return None
    return prefix


def _seconds(minimum: float, maximum: float, step: float) -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            min=minimum,
            max=maximum,
            step=step,
            unit_of_measurement="s",
            mode=NumberSelectorMode.BOX,
        )
    )


def _select(options: tuple[str, ...], key: str) -> SelectSelector:
    return SelectSelector(
        SelectSelectorConfig(
            options=list(options), translation_key=key, mode=SelectSelectorMode.DROPDOWN
        )
    )


def _any_number() -> NumberSelector:
    return NumberSelector(NumberSelectorConfig(mode=NumberSelectorMode.BOX, step="any"))


def _gradient_fields() -> dict[Any, Any]:
    """Gradient settings; a custom gradient's stops are edited in the panel."""
    return {
        vol.Optional(CONF_GRADIENT): _select(GRADIENTS, CONF_GRADIENT),
        vol.Optional(CONF_GRADIENT_MIN): _any_number(),
        vol.Optional(CONF_GRADIENT_MAX): _any_number(),
    }


class AwtrixStudioConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add a clock."""

    VERSION = 1
    MINOR_VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the firmware and the MQTT prefix."""
        if not self.hass.config_entries.async_entries(MQTT_DOMAIN):
            return self.async_abort(reason="mqtt_not_configured")

        errors: dict[str, str] = {}
        if user_input is not None:
            prefix = _normalize_prefix(user_input[CONF_PREFIX])
            firmware = user_input[CONF_FIRMWARE]
            if prefix is None:
                errors[CONF_PREFIX] = "invalid_prefix"
            else:
                await self.async_set_unique_id(prefix)
                self._abort_if_unique_id_configured()
                if firmware == FIRMWARE_AUTO:
                    firmware = await async_detect_firmware(self.hass, prefix)
                if firmware is None:
                    errors[CONF_FIRMWARE] = "not_detected"
                else:
                    return self.async_create_entry(
                        title=prefix,
                        data={CONF_FIRMWARE: firmware, CONF_PREFIX: prefix},
                    )

        schema = vol.Schema(
            {
                vol.Required(CONF_FIRMWARE, default=FIRMWARE_AUTO): SelectSelector(
                    SelectSelectorConfig(
                        options=[FIRMWARE_AUTO, *DRIVERS],
                        translation_key=CONF_FIRMWARE,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(CONF_PREFIX, default=DEFAULT_PREFIX): TextSelector(),
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(schema, user_input),
            errors=errors,
        )

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Apps and indicators are subentries of a clock."""
        return {
            SUBENTRY_TYPE_APP: AppSubentryFlow,
            SUBENTRY_TYPE_INDICATOR: IndicatorSubentryFlow,
        }


class _LiveSubentryFlow(ConfigSubentryFlow):
    """Shared logic: a menu to pick simple or template mode, then a form."""

    # Values to pre-fill the next form with (edit, or conversion to template).
    _suggested: dict[str, Any] | None = None

    @property
    def _is_new(self) -> bool:
        return self.source == "user"

    @property
    def _current(self) -> dict[str, Any]:
        return dict(self._get_reconfigure_subentry().data)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Choose how to describe the new item."""
        return self.async_show_menu(
            step_id="user", menu_options=[MODE_SIMPLE, MODE_TEMPLATE]
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Edit an item; a simple one can also be turned into a template."""
        self._suggested = self._current
        if self._current.get(CONF_MODE) == MODE_SIMPLE:
            return self.async_show_menu(
                step_id="reconfigure", menu_options=[MODE_SIMPLE, "convert"]
            )
        return await self.async_step_template()

    async def async_step_convert(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Open the generated template in template mode."""
        current = self._current
        self._suggested = {**current, CONF_TEMPLATE: self._build_template(current)}
        return await self.async_step_template()

    # -- to implement ------------------------------------------------------

    def _build_template(self, data: dict[str, Any]) -> str:
        raise NotImplementedError

    # -- helpers -----------------------------------------------------------

    def _starter_template(self) -> str:
        firmware = self._get_entry().data[CONF_FIRMWARE]
        starters = STARTER_TEMPLATES.get(firmware, STARTER_TEMPLATES[FIRMWARE_AWTRIX3])
        return starters[self._subentry_type]

    def _siblings(self) -> list[dict[str, Any]]:
        """Data of the other subentries of the same type."""
        own = None if self._is_new else self._reconfigure_subentry_id
        return [
            dict(subentry.data)
            for subentry_id, subentry in self._get_entry().subentries.items()
            if subentry.subentry_type == self._subentry_type and subentry_id != own
        ]

    def _validate_template(self, template_str: str) -> tuple[str, str] | None:
        """Render the template once; return (error key, detail) if it fails."""
        template = Template(template_str, self.hass)
        try:
            template.ensure_valid()
            result = template.async_render(parse_result=True)
        except TemplateError as err:
            return "invalid_template", str(err)
        try:
            parse_pages(result)
        except InvalidPagesError as err:
            return "invalid_pages", str(err)
        return None

    def _check_gradient(self, data: dict[str, Any], errors: dict[str, str]) -> None:
        """Check the gradient settings and keep only what describes it."""
        data.setdefault(CONF_GRADIENT, DEFAULT_GRADIENT)
        if data[CONF_GRADIENT] == GRADIENT_CUSTOM:
            stops = None if self._is_new else self._current.get(CONF_GRADIENT_STOPS)
            if not stops:
                errors[CONF_GRADIENT] = "custom_in_panel"
                return
            data[CONF_GRADIENT_STOPS] = stops
        try:
            resolved = resolve_stops(data)
        except InvalidGradientError as err:
            field = err.field if err.field != CONF_GRADIENT_STOPS else CONF_GRADIENT
            errors[field] = err.key
            return
        if data[CONF_GRADIENT] == GRADIENT_CUSTOM:
            data[CONF_GRADIENT_STOPS] = stops_to_data(resolved)
        else:
            data.pop(CONF_GRADIENT_STOPS, None)

    def _show(
        self,
        step_id: str,
        schema: vol.Schema,
        user_input: dict[str, Any] | None,
        errors: dict[str, str],
        placeholders: dict[str, str] | None = None,
    ) -> SubentryFlowResult:
        suggested = user_input if user_input is not None else self._suggested
        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(schema, suggested),
            errors=errors,
            description_placeholders=placeholders or {"error": ""},
        )

    def _save(
        self, title: str, unique_id: str, data: dict[str, Any]
    ) -> SubentryFlowResult:
        if self._is_new:
            return self.async_create_entry(title=title, data=data, unique_id=unique_id)
        # Display options are only edited in the panel: keep them.
        if display := self._current.get(CONF_DISPLAY):
            data = {**data, CONF_DISPLAY: display}
        # So is the layout of a simple app on a TC002, with its fonts.
        if data.get(CONF_MODE) == MODE_SIMPLE and self._current.get(CONF_LAYOUT):
            data = {
                **data,
                **{
                    key: self._current[key]
                    for key in (CONF_LAYOUT, CONF_VALUE_FONT, CONF_TEXT_FONT)
                    if key in self._current
                },
            }
        # So are the next entities of a simple app: this form edits the first.
        if data.get(CONF_MODE) == MODE_SIMPLE and self._current.get(CONF_SEGMENTS):
            data = {
                **data,
                CONF_SEGMENTS: self._current[CONF_SEGMENTS],
                CONF_SEPARATOR: self._current.get(CONF_SEPARATOR, DEFAULT_SEPARATOR),
            }
        return self.async_update_and_abort(
            self._get_entry(), self._get_reconfigure_subentry(), data=data
        )


class AppSubentryFlow(_LiveSubentryFlow):
    """Add or edit an app."""

    def _build_template(self, data: dict[str, Any]) -> str:
        return build_app_template(data, self._get_entry().data[CONF_FIRMWARE])

    def _check_name(self, data: dict[str, Any], errors: dict[str, str]) -> None:
        if not self._is_new:
            data[CONF_APP_NAME] = self._current[CONF_APP_NAME]
            return
        name = str(data.get(CONF_APP_NAME, "")).strip()
        data[CONF_APP_NAME] = name
        if not re.fullmatch(APP_NAME_PATTERN, name):
            errors[CONF_APP_NAME] = "invalid_app_name"
        elif any(other.get(CONF_APP_NAME) == name for other in self._siblings()):
            errors[CONF_APP_NAME] = "app_name_exists"

    def _name_field(self) -> dict[Any, Any]:
        return {vol.Required(CONF_APP_NAME): TextSelector()} if self._is_new else {}

    async def async_step_simple(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """One entity shown as text, with an icon and colors."""
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {CONF_MODE: MODE_SIMPLE, **user_input}
            self._check_name(data, errors)
            data[CONF_COLOR_MODE] = app_color_mode(data)
            if data[CONF_COLOR_MODE] == COLOR_GRADIENT:
                self._check_gradient(data, errors)
            for threshold, color in (
                (CONF_THRESHOLD_1, CONF_COLOR_1),
                (CONF_THRESHOLD_2, CONF_COLOR_2),
            ):
                if data[CONF_COLOR_MODE] != COLOR_STEPS:
                    break
                if data.get(threshold) is not None and not data.get(color):
                    errors[color] = "color_required"
            if data.get(CONF_PROGRESS) and float(
                data.get(CONF_PROGRESS_MAX, DEFAULT_PROGRESS_MAX)
            ) <= float(data.get(CONF_PROGRESS_MIN, DEFAULT_PROGRESS_MIN)):
                errors[CONF_PROGRESS_MAX] = "invalid_range"
            if not errors:
                return self._save(data[CONF_APP_NAME], data[CONF_APP_NAME], data)

        schema = vol.Schema(
            {
                **self._name_field(),
                vol.Required(CONF_ENTITY_ID): EntitySelector(),
                vol.Optional(CONF_LABEL): TextSelector(),
                vol.Optional(CONF_ICON): TextSelector(),
                vol.Required(CONF_COLOR, default=DEFAULT_COLOR): ColorRGBSelector(),
                vol.Optional(CONF_DECIMALS): NumberSelector(
                    NumberSelectorConfig(
                        min=0, max=4, step=1, mode=NumberSelectorMode.BOX
                    )
                ),
                vol.Required(CONF_SHOW_UNIT, default=True): BooleanSelector(),
                vol.Required(CONF_HIDE_WHEN_ZERO, default=False): BooleanSelector(),
                vol.Optional(CONF_COLOR_MODE): _select(
                    APP_COLOR_MODES, CONF_COLOR_MODE
                ),
                vol.Optional(CONF_THRESHOLD_1): _any_number(),
                vol.Optional(CONF_COLOR_1): ColorRGBSelector(),
                vol.Optional(CONF_THRESHOLD_2): _any_number(),
                vol.Optional(CONF_COLOR_2): ColorRGBSelector(),
                **_gradient_fields(),
                vol.Required(CONF_PROGRESS, default=False): BooleanSelector(),
                vol.Optional(CONF_PROGRESS_ENTITY): EntitySelector(),
                vol.Required(
                    CONF_PROGRESS_MIN, default=DEFAULT_PROGRESS_MIN
                ): _any_number(),
                vol.Required(
                    CONF_PROGRESS_MAX, default=DEFAULT_PROGRESS_MAX
                ): _any_number(),
                vol.Optional(CONF_PROGRESS_COLOR): ColorRGBSelector(),
                vol.Required(
                    CONF_PROGRESS_BG, default=DEFAULT_PROGRESS_BG
                ): ColorRGBSelector(),
            }
        )
        return self._show("simple", schema, user_input, errors)

    async def async_step_template(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """A template rendering the app's JSON, one or several pages."""
        errors: dict[str, str] = {}
        placeholders = {"error": ""}
        if user_input is not None:
            data = {CONF_MODE: MODE_TEMPLATE, **user_input}
            self._check_name(data, errors)
            if error := self._validate_template(data[CONF_TEMPLATE]):
                errors[CONF_TEMPLATE], placeholders["error"] = error
            data[CONF_LIFETIME] = int(data.get(CONF_LIFETIME, DEFAULT_LIFETIME))
            if not errors:
                return self._save(data[CONF_APP_NAME], data[CONF_APP_NAME], data)
        elif self._suggested is None:
            self._suggested = {CONF_TEMPLATE: self._starter_template()}

        schema = vol.Schema(
            {
                **self._name_field(),
                vol.Required(CONF_TEMPLATE): TemplateSelector(),
                vol.Required(
                    CONF_PAGE_INTERVAL, default=DEFAULT_PAGE_INTERVAL
                ): _seconds(MIN_PAGE_INTERVAL, MAX_PAGE_INTERVAL, 0.1),
                vol.Required(
                    CONF_CYCLE_ONLY_ON_SCREEN, default=DEFAULT_CYCLE_ONLY_ON_SCREEN
                ): BooleanSelector(),
                vol.Required(
                    CONF_HIDE_WHEN_EMPTY, default=DEFAULT_HIDE_WHEN_EMPTY
                ): BooleanSelector(),
                vol.Required(CONF_LIFETIME, default=DEFAULT_LIFETIME): _seconds(
                    0, MAX_LIFETIME, 1
                ),
            }
        )
        return self._show("template", schema, user_input, errors, placeholders)


class IndicatorSubentryFlow(_LiveSubentryFlow):
    """Add or edit one of the clock's three colored indicators."""

    def _build_template(self, data: dict[str, Any]) -> str:
        return build_indicator_template(data, self._get_entry().data[CONF_FIRMWARE])

    def _check_indicator(self, data: dict[str, Any], errors: dict[str, str]) -> None:
        if not self._is_new:
            data[CONF_INDICATOR] = self._current[CONF_INDICATOR]
            return
        if any(o.get(CONF_INDICATOR) == data[CONF_INDICATOR] for o in self._siblings()):
            errors[CONF_INDICATOR] = "indicator_in_use"

    def _indicator_field(self) -> dict[Any, Any]:
        if not self._is_new:
            return {}
        return {
            vol.Required(CONF_INDICATOR): SelectSelector(
                SelectSelectorConfig(
                    options=list(INDICATORS),
                    translation_key=CONF_INDICATOR,
                    mode=SelectSelectorMode.LIST,
                )
            )
        }

    def _save_indicator(self, data: dict[str, Any]) -> SubentryFlowResult:
        number = data[CONF_INDICATOR]
        return self._save(f"Indicator {number}", f"indicator:{number}", data)

    async def async_step_simple(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Lit with a color while an entity is in a given state."""
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {CONF_MODE: MODE_SIMPLE, **user_input}
            self._check_indicator(data, errors)
            data[CONF_COLOR_MODE] = indicator_color_mode(data)
            if data[CONF_COLOR_MODE] == COLOR_GRADIENT:
                self._check_gradient(data, errors)
            if not errors:
                return self._save_indicator(data)

        schema = vol.Schema(
            {
                **self._indicator_field(),
                vol.Required(CONF_ENTITY_ID): EntitySelector(),
                vol.Optional(CONF_COLOR_MODE): _select(
                    INDICATOR_COLOR_MODES, "indicator_color_mode"
                ),
                vol.Required(
                    CONF_ACTIVE_STATE, default=DEFAULT_ACTIVE_STATE
                ): TextSelector(),
                vol.Required(
                    CONF_COLOR, default=DEFAULT_INDICATOR_COLOR
                ): ColorRGBSelector(),
                **_gradient_fields(),
                vol.Required(CONF_EFFECT, default=EFFECT_NONE): SelectSelector(
                    SelectSelectorConfig(
                        options=list(EFFECTS),
                        translation_key=CONF_EFFECT,
                        mode=SelectSelectorMode.LIST,
                    )
                ),
                vol.Required(
                    CONF_EFFECT_INTERVAL, default=DEFAULT_EFFECT_INTERVAL
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=100,
                        max=10000,
                        step=100,
                        unit_of_measurement="ms",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
            }
        )
        return self._show("simple", schema, user_input, errors)

    async def async_step_template(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """A template rendering the indicator's JSON."""
        errors: dict[str, str] = {}
        placeholders = {"error": ""}
        if user_input is not None:
            data = {CONF_MODE: MODE_TEMPLATE, **user_input}
            self._check_indicator(data, errors)
            if error := self._validate_template(data[CONF_TEMPLATE]):
                errors[CONF_TEMPLATE], placeholders["error"] = error
            if not errors:
                return self._save_indicator(data)
        elif self._suggested is None:
            self._suggested = {CONF_TEMPLATE: self._starter_template()}

        schema = vol.Schema(
            {
                **self._indicator_field(),
                vol.Required(CONF_TEMPLATE): TemplateSelector(),
            }
        )
        return self._show("template", schema, user_input, errors, placeholders)
