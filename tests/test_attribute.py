"""An app that shows an attribute of an entity instead of its state."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.awtrix_studio.const import DOMAIN
from custom_components.awtrix_studio.templates import (
    build_app_template,
    build_lines_template,
)
from custom_components.awtrix_studio.validation import Invalid, normalize_app

from .test_templates import render

WEATHER = "weather.home"


def options(**extra: Any) -> dict[str, Any]:
    """The temperature of a weather entity."""
    return {
        "entity_id": WEATHER,
        "attribute": "temperature",
        "icon": "lametric:9",
        "decimals": 1,
        "unit": "°",
        **extra,
    }


@pytest.mark.parametrize("firmware", ["awtrix3", "awtrix_ng"])
async def test_value_from_an_attribute(hass: HomeAssistant, firmware: str) -> None:
    """The temperature of a weather entity is one of its attributes."""
    template = build_app_template(options(), firmware)
    hass.states.async_set(WEATHER, "rainy", {"temperature": 5.64})
    assert render(hass, template)[0]["text"] == "5.6°"
    # No such attribute (or no entity yet): the app is hidden, not "None".
    hass.states.async_set(WEATHER, "rainy", {})
    assert render(hass, template) == []
    hass.states.async_remove(WEATHER)
    assert render(hass, template) == []


async def test_the_state_is_still_the_default(hass: HomeAssistant) -> None:
    """Without an attribute, the app reads the state, as before."""
    template = build_app_template(options(attribute=""), "awtrix3")
    hass.states.async_set(WEATHER, "5.64", {"temperature": 1})
    assert render(hass, template)[0]["text"] == "5.6°"


async def test_each_entity_of_a_line_has_its_attribute(hass: HomeAssistant) -> None:
    """Two entities, one read by its attribute, one by its state."""
    template = build_app_template(
        {
            **options(),
            "separator": " ",
            "segments": [{"entity_id": "sensor.in", "decimals": 0}],
        },
        "awtrix3",
    )
    hass.states.async_set(WEATHER, "rainy", {"temperature": 5.64})
    hass.states.async_set("sensor.in", "21")
    [page] = render(hass, template)
    assert "".join(part["t"] for part in page["text"]) == "5.6° 21"


async def test_a_notification_reads_an_attribute_too(hass: HomeAssistant) -> None:
    """Notifications share the simple form."""
    template = build_lines_template({**options(), "layout": "line"})
    hass.states.async_set(WEATHER, "rainy", {"temperature": 5.64})
    assert "5.6°" in str(render(hass, template))


def entry() -> MockConfigEntry:
    """A clock to validate against."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="clock",
        data={"firmware": "awtrix3", "prefix": "clock"},
    )


async def test_validation_keeps_the_attribute(hass: HomeAssistant) -> None:
    """The attribute is kept, and dropped when it is empty."""
    clock = entry()
    clock.add_to_hass(hass)
    data = normalize_app(
        hass, clock, {"mode": "simple", "app_name": "Temp", **options()}, None
    )
    assert data["attribute"] == "temperature"
    data = normalize_app(
        hass,
        clock,
        {"mode": "simple", "app_name": "Temp", **options(attribute="")},
        None,
    )
    assert "attribute" not in data


@pytest.mark.parametrize("attribute", ["no spaces", "a.b", 5])
async def test_validation_refuses_a_bad_attribute(
    hass: HomeAssistant, attribute: Any
) -> None:
    """An attribute is a name, as Home Assistant has them."""
    clock = entry()
    clock.add_to_hass(hass)
    with pytest.raises(Invalid) as err:
        normalize_app(
            hass,
            clock,
            {"mode": "simple", "app_name": "Temp", **options(attribute=attribute)},
            None,
        )
    assert err.value.field == "attribute"


async def test_the_unit_of_an_attribute_is_its_own(hass: HomeAssistant) -> None:
    """`<attribute>_unit` when Home Assistant gives one; never the entity's unit."""
    template = build_app_template(options(unit="", decimals=0), "awtrix3")
    hass.states.async_set(
        WEATHER,
        "rainy",
        {"temperature": 5.6, "temperature_unit": "°C", "unit_of_measurement": "%"},
    )
    assert render(hass, template)[0]["text"] == "6°C"
    hass.states.async_set(
        WEATHER, "rainy", {"temperature": 5.6, "unit_of_measurement": "%"}
    )
    assert render(hass, template)[0]["text"] == "6"
    # Without an attribute the entity's unit is used, as before.
    state = build_app_template(options(unit="", decimals=0, attribute=""), "awtrix3")
    hass.states.async_set("weather.home", "5.6", {"unit_of_measurement": "%"})
    assert render(hass, state)[0]["text"] == "6%"


@pytest.mark.parametrize("firmware", ["awtrix3", "awtrix_ng"])
async def test_the_progress_bar_follows_the_attribute(
    hass: HomeAssistant, firmware: str
) -> None:
    """Without a progress entity, the bar reads the value the text shows."""
    template = build_app_template(
        options(progress=True, progress_min=0, progress_max=40), firmware
    )
    hass.states.async_set(WEATHER, "rainy", {"temperature": 10})
    [page] = render(hass, template)
    assert page["progress"] == 25


async def test_the_progress_bar_of_a_layout_follows_the_attribute(
    hass: HomeAssistant,
) -> None:
    """The layout with a bar reads the attribute too."""
    template = build_app_template(
        options(layout="value_bar", progress_min=0, progress_max=40), "awtrix_ng"
    )
    hass.states.async_set(WEATHER, "rainy", {"temperature": 30})
    [page] = render(hass, template)
    [bar] = [r for r in page["layout"]["regions"] if r["id"] == "bar"]
    assert bar["progress"] == 75
