"""Tests for simple-mode template generation, rendered by Home Assistant."""

from __future__ import annotations

import json
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.template import Template
import pytest

from custom_components.awtrix_studio.pages import parse_pages
from custom_components.awtrix_studio.templates import (
    InvalidSimpleOptionsError,
    build_app_template,
    build_indicator_template,
    rgb_to_hex,
)


def render(hass: HomeAssistant, template: str) -> list[dict[str, Any]]:
    """Render like the engine does and return the pages."""
    return parse_pages(Template(template, hass).async_render(parse_result=True))


def test_rgb_to_hex() -> None:
    """Colors are normalized to #RRGGBB."""
    assert rgb_to_hex([136, 212, 152], [0, 0, 0]) == "#88D498"
    assert rgb_to_hex("#ff9999", [0, 0, 0]) == "#FF9999"
    assert rgb_to_hex(None, [255, 255, 255]) == "#FFFFFF"
    assert rgb_to_hex([300, -5, 12], [0, 0, 0]) == "#FF000C"


async def test_co2_with_thresholds(hass: HomeAssistant) -> None:
    """Two thresholds pick the color, the unit follows the value."""
    template = build_app_template(
        {
            "entity_id": "sensor.co2",
            "icon": "43980",
            "color": [136, 212, 152],
            "decimals": 0,
            "show_unit": True,
            "threshold_1": 800,
            "color_1": [255, 209, 128],
            "threshold_2": 1200,
            "color_2": [255, 153, 153],
        }
    )
    attrs = {"unit_of_measurement": "ppm"}

    hass.states.async_set("sensor.co2", "650.4", attrs)
    assert render(hass, template) == [
        {"text": "650 ppm", "color": "#88D498", "icon": "43980"}
    ]
    hass.states.async_set("sensor.co2", "950", attrs)
    assert render(hass, template)[0]["color"] == "#FFD180"
    hass.states.async_set("sensor.co2", "1500", attrs)
    assert render(hass, template)[0]["color"] == "#FF9999"


@pytest.mark.parametrize(
    ("state", "unit", "options", "text"),
    [
        ("21.537", "°C", {"decimals": 1}, "21.5°C"),
        ("54", "%", {}, "54%"),
        ("0.42", "kW", {}, "0.42 kW"),
        ("0.42", "kW", {"show_unit": False}, "0.42"),
        ("1.0", "µg/m³", {"show_unit": False, "decimals": 0}, "1"),
        ("12", "km", {"label": "Nico"}, "Nico 12 km"),
        ("idle", None, {}, "idle"),
        ("3.0", None, {"decimals": 0}, "3"),
        # A custom unit replaces the entity's, or gives one to a bare number.
        ("750", None, {"unit": "GB"}, "750 GB"),
        ("0.42", "kW", {"unit": "kWh"}, "0.42 kWh"),
        ("54", "%", {"unit": "°"}, "54°"),
        ("12", "km", {"unit": '"}}{{'}, '12 "}}{{'),
        ("0.42", "kW", {"unit": "kWh", "show_unit": False}, "0.42"),
    ],
)
async def test_text_format(
    hass: HomeAssistant,
    state: str,
    unit: str | None,
    options: dict[str, Any],
    text: str,
) -> None:
    """Label, value, decimals and unit are combined like Home Assistant does."""
    attrs = {"unit_of_measurement": unit} if unit else {}
    hass.states.async_set("sensor.value", state, attrs)
    template = build_app_template({"entity_id": "sensor.value", **options})
    assert render(hass, template)[0]["text"] == text


async def test_hidden_when_unavailable_or_zero(hass: HomeAssistant) -> None:
    """No page while unavailable; optionally none while zero."""
    plain = build_app_template({"entity_id": "sensor.pv"})
    hide_zero = build_app_template({"entity_id": "sensor.pv", "hide_when_zero": True})

    hass.states.async_set("sensor.pv", "unavailable")
    assert render(hass, plain) == []
    hass.states.async_set("sensor.pv", "0")
    assert render(hass, plain) == [{"text": "0", "color": "#FFFFFF"}]
    assert render(hass, hide_zero) == []
    hass.states.async_set("sensor.pv", "1.2")
    assert render(hass, hide_zero) == [{"text": "1.2", "color": "#FFFFFF"}]


async def test_label_with_quotes_is_escaped(hass: HomeAssistant) -> None:
    """User text cannot break the template."""
    hass.states.async_set("sensor.value", "1")
    template = build_app_template(
        {"entity_id": "sensor.value", "label": 'Say "hi" {{ x }}', "icon": "a'b"}
    )
    page = render(hass, template)[0]
    assert page["text"] == 'Say "hi" {{ x }} 1'
    assert page["icon"] == "a'b"


@pytest.mark.parametrize(
    ("effect", "expected"),
    [
        ("none", {"color": "#D357FE"}),
        ("fade", {"color": "#D357FE", "fade": 3000}),
        ("blink", {"color": "#D357FE", "blink": 3000}),
    ],
)
async def test_indicator(
    hass: HomeAssistant, effect: str, expected: dict[str, Any]
) -> None:
    """A simple indicator is lit only in the chosen state."""
    template = build_indicator_template(
        {
            "entity_id": "input_boolean.mail",
            "active_state": "on",
            "color": [211, 87, 254],
            "effect": effect,
            "effect_interval": 3000,
        }
    )
    hass.states.async_set("input_boolean.mail", "on")
    assert render(hass, template) == [expected]
    hass.states.async_set("input_boolean.mail", "off")
    assert render(hass, template) == []


def test_generated_template_is_readable() -> None:
    """Converting to template mode shows something a human can edit."""
    template = build_app_template({"entity_id": "sensor.co2", "label": "CO2"})
    assert template.startswith("{#- Generated by Awtrix Studio")
    assert json.dumps("sensor.co2") in template


# ------------------------------------------------------------------ security

INJECTIONS = [
    "{{ 7 * 7 }}",
    '"}} {{ 7 * 7 }} {{"',
    "%}{% set x = 1 %}{%",
    '" -%}{{ states.sensor | list | count }}{%- if "',
    "{# comment #}",
    '\\" ~ 7 * 7 ~ \\"',
    "line\nbreak",
]


@pytest.mark.parametrize("payload", INJECTIONS)
async def test_user_text_is_never_executed(hass: HomeAssistant, payload: str) -> None:
    """Label and icon end up verbatim on the clock, never evaluated."""
    hass.states.async_set("sensor.value", "1")
    template = build_app_template(
        {"entity_id": "sensor.value", "label": payload, "icon": payload}
    )
    page = render(hass, template)[0]
    assert page["text"] == f"{payload.strip()} 1"
    assert page["icon"] == payload.strip()


@pytest.mark.parametrize("payload", INJECTIONS)
async def test_indicator_state_is_never_executed(
    hass: HomeAssistant, payload: str
) -> None:
    """The active state is compared literally."""
    hass.states.async_set("input_boolean.x", "on")
    template = build_indicator_template(
        {"entity_id": "input_boolean.x", "active_state": payload, "color": [1, 2, 3]}
    )
    assert render(hass, template) == []


@pytest.mark.parametrize(
    "entity_id",
    ["sensor.x') }}{{ 7*7 }}", "sensor", "Sensor.X", "sensor.x y", "", None],
)
def test_invalid_entity_id_is_refused(entity_id: object) -> None:
    """Only well-formed entity ids reach a template."""
    with pytest.raises(InvalidSimpleOptionsError):
        build_app_template({"entity_id": entity_id})
    with pytest.raises(InvalidSimpleOptionsError):
        build_indicator_template({"entity_id": entity_id})


@pytest.mark.parametrize(
    "color",
    ['#FFF" }}{{ 7*7 }}', "#GGGGGG", "red", "#FFF", [1, 2], ["a", 2, 3], 42],
)
def test_invalid_colors_fall_back(color: object) -> None:
    """Anything that is not a clean color becomes the default color."""
    assert rgb_to_hex(color, [255, 255, 255]) == "#FFFFFF"


def test_numbers_are_clamped() -> None:
    """Decimals, effects and intervals stay within safe bounds."""
    app = build_app_template({"entity_id": "sensor.x", "decimals": 99})
    assert "round(4)" in app
    indicator = build_indicator_template(
        {"entity_id": "sensor.x", "effect": "evil", "effect_interval": 10**9}
    )
    assert "evil" not in indicator


async def test_ng_templates_use_ng_keys(hass: HomeAssistant) -> None:
    """Simple items describe what to show; NG clocks get NG keys."""
    hass.states.async_set("sensor.battery", "40", {"unit_of_measurement": "%"})
    template = build_app_template(
        {
            "entity_id": "sensor.battery",
            "color": [255, 255, 255],
            "progress": True,
            "progress_bg": [0, 0, 0],
        },
        "awtrix_ng",
    )
    assert render(hass, template) == [
        {
            "text": "40%",
            "textColor": "#FFFFFF",
            "progress": 40,
            "progressColor": "#FFFFFF",
            "progressTrackColor": "#000000",
        }
    ]

    hass.states.async_set("binary_sensor.door", "on")
    template = build_indicator_template(
        {"entity_id": "binary_sensor.door", "effect": "blink", "effect_interval": 500},
        "awtrix_ng",
    )
    assert render(hass, template) == [{"color": "#FF8C00", "blinkMs": 500}]


LINE = {
    "entity_id": "sensor.up",
    "label": "UP",
    "color": [0, 255, 0],
    "decimals": 0,
    "segments": [
        {
            "entity_id": "sensor.down",
            "label": "DN",
            "color_mode": "steps",
            "threshold_1": 50,
            "color_1": [255, 0, 0],
        }
    ],
}


async def test_line_of_entities(hass: HomeAssistant) -> None:
    """Several entities share the line, each in its own color."""
    hass.states.async_set("sensor.up", "750.2", {"unit_of_measurement": "Mbit/s"})
    hass.states.async_set("sensor.down", "30", {})
    template = build_app_template({**LINE, "icon": "1234"})
    assert render(hass, template) == [
        {
            "text": [
                {"t": "UP 750 Mbit/s", "c": "00FF00"},
                {"t": " DN 30", "c": "FFFFFF"},
            ],
            "icon": "1234",
        }
    ]
    hass.states.async_set("sensor.down", "80", {})
    assert render(hass, template)[0]["text"][1] == {"t": " DN 80", "c": "FF0000"}


async def test_line_gradient_color_is_hex(hass: HomeAssistant) -> None:
    """A gradient color (an RGB list) reaches AWTRIX 3 as bare hex digits."""
    hass.states.async_set("sensor.up", "1", {})
    hass.states.async_set("sensor.down", "100", {})
    segment = {
        "entity_id": "sensor.down",
        "color_mode": "gradient",
        "gradient": "custom",
        "gradient_stops": [
            {"value": 0, "color": [0, 0, 255]},
            {"value": 100, "color": [255, 0, 0]},
        ],
    }
    template = build_app_template({**LINE, "segments": [segment]})
    assert render(hass, template)[0]["text"][1]["c"] == "FF0000"


async def test_line_separator_and_missing_values(hass: HomeAssistant) -> None:
    """An entity without a value leaves the line; none left hides the app."""
    options = {
        **LINE,
        "show_unit": False,
        "separator": " | ",
        "segments": [{**LINE["segments"][0], "hide_when_zero": True}],
    }
    template = build_app_template(options)
    hass.states.async_set("sensor.up", "1", {})
    hass.states.async_set("sensor.down", "2", {})
    assert [p["t"] for p in render(hass, template)[0]["text"]] == ["UP 1", " | DN 2"]
    hass.states.async_set("sensor.down", "0", {})
    assert [p["t"] for p in render(hass, template)[0]["text"]] == ["UP 1"]
    hass.states.async_set("sensor.up", "unavailable", {})
    hass.states.async_set("sensor.down", "3", {})
    # The separator only goes between two entities shown.
    assert [p["t"] for p in render(hass, template)[0]["text"]] == ["DN 3"]
    hass.states.async_set("sensor.down", "unknown", {})
    assert render(hass, template) == []


async def test_line_on_awtrix_ng(hass: HomeAssistant) -> None:
    """AWTRIX NG gets one text, in the first color, and its own keys."""
    hass.states.async_set("sensor.up", "750", {})
    hass.states.async_set("sensor.down", "30", {})
    template = build_app_template(
        {**LINE, "progress": True, "progress_max": 1000}, "awtrix_ng"
    )
    assert render(hass, template) == [
        {
            "text": "UP 750 DN 30",
            "textColor": "#00FF00",
            "progress": 75,
            "progressColor": "#00FF00",
            "progressTrackColor": "#2E2E2E",
        }
    ]


def test_line_rejects_unsafe_entities() -> None:
    """The next entities are checked like the first one."""
    with pytest.raises(InvalidSimpleOptionsError):
        build_app_template({**LINE, "segments": [{"entity_id": "x') }}"}]})
