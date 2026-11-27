"""An app's icon: fixed, by the state of an entity, or by a value."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigSubentry
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.awtrix_studio.const import DOMAIN
from custom_components.awtrix_studio.icons import IconStore
from custom_components.awtrix_studio.manager import app_config_from_subentry
from custom_components.awtrix_studio.templates import (
    build_app_template,
    icon_mode,
    icon_names,
)
from custom_components.awtrix_studio.validation import Invalid, normalize_app

from .test_templates import render

WEATHER = "weather.home"
ICONS = {"sunny": "lametric:1", "rainy": "lametric:2", "snowy": "lametric:3"}
STEPS = [
    {"min": 0, "icon": "lametric:10"},
    {"min": 20, "icon": "lametric:20"},
    {"min": 50, "icon": "lametric:50"},
    {"min": 95, "icon": "lametric:95"},
]


def by_state(**extra: Any) -> dict[str, Any]:
    """A temperature whose icon follows the weather."""
    return {
        "entity_id": "sensor.outside",
        "icon": "lametric:9",
        "icon_mode": "state",
        "icon_entity_id": WEATHER,
        "icon_states": ICONS,
        "decimals": 0,
        "show_unit": False,
        **extra,
    }


def by_value(**extra: Any) -> dict[str, Any]:
    """A battery whose icon follows its own level."""
    return {
        "entity_id": "sensor.battery",
        "icon": "lametric:9",
        "icon_mode": "value",
        "icon_steps": STEPS,
        "decimals": 0,
        **extra,
    }


@pytest.mark.parametrize("firmware", ["awtrix3", "awtrix_ng"])
async def test_icon_by_state(hass: HomeAssistant, firmware: str) -> None:
    """Each state brings its icon; any other state brings the default one."""
    template = build_app_template(by_state(), firmware)
    hass.states.async_set("sensor.outside", "5.6")
    for state, icon in [
        ("sunny", "lametric:1"),
        ("rainy", "lametric:2"),
        ("snowy", "lametric:3"),
        ("fog", "lametric:9"),
        ("unavailable", "lametric:9"),
    ]:
        hass.states.async_set(WEATHER, state)
        [page] = render(hass, template)
        assert page["icon"] == icon, state
        assert page["text"] == "6"


async def test_icon_by_state_of_the_app_itself(hass: HomeAssistant) -> None:
    """Without an entity of its own, the icon follows the app's entity."""
    template = build_app_template(
        {
            "entity_id": "binary_sensor.door",
            "icon": "lametric:9",
            "icon_mode": "state",
            "icon_states": {"on": "lametric:1", "off": "lametric:2"},
        },
        "awtrix3",
    )
    hass.states.async_set("binary_sensor.door", "on")
    assert render(hass, template)[0]["icon"] == "lametric:1"
    hass.states.async_set("binary_sensor.door", "off")
    assert render(hass, template)[0]["icon"] == "lametric:2"


async def test_icon_by_state_on_a_layout(hass: HomeAssistant) -> None:
    """The icon region of a TC002 layout follows the state too."""
    template = build_app_template(by_state(layout="value"), "awtrix_ng")
    hass.states.async_set("sensor.outside", "5.6")
    hass.states.async_set(WEATHER, "snowy")
    [page] = render(hass, template)
    [icon] = [r for r in page["layout"]["regions"] if r["id"] == "icon"]
    assert icon["icon"] == "lametric:3"


@pytest.mark.parametrize("firmware", ["awtrix3", "awtrix_ng"])
@pytest.mark.parametrize(
    ("value", "icon"),
    [
        ("-5", "lametric:9"),
        ("0", "lametric:10"),
        ("19.9", "lametric:10"),
        ("20", "lametric:20"),
        ("49", "lametric:20"),
        ("50", "lametric:50"),
        ("94.9", "lametric:50"),
        ("95", "lametric:95"),
        ("100", "lametric:95"),
        ("unavailable", "lametric:9"),
        ("unknown", "lametric:9"),
        ("charging", "lametric:9"),
    ],
)
async def test_icon_by_value(
    hass: HomeAssistant, firmware: str, value: str, icon: str
) -> None:
    """The step the value reaches; the default below the first, or if not a number."""
    template = build_app_template(by_value(), firmware)
    hass.states.async_set("sensor.battery", value)
    pages = render(hass, template)
    if value in ("unavailable", "unknown"):
        assert pages == []
        return
    assert pages[0]["icon"] == icon


async def test_icon_by_value_of_another_entity(hass: HomeAssistant) -> None:
    """The value can be another entity's: the level of a battery, say."""
    template = build_app_template(
        by_value(entity_id="sensor.name", icon_entity_id="sensor.level"), "awtrix3"
    )
    hass.states.async_set("sensor.name", "phone")
    hass.states.async_set("sensor.level", "60")
    assert render(hass, template)[0]["icon"] == "lametric:50"
    hass.states.async_set("sensor.level", "n/a")
    assert render(hass, template)[0]["icon"] == "lametric:9"


async def test_icon_by_value_of_an_attribute(hass: HomeAssistant) -> None:
    """The app's own value is the attribute it shows."""
    template = build_app_template(
        by_value(entity_id=WEATHER, attribute="temperature"), "awtrix3"
    )
    hass.states.async_set(WEATHER, "rainy", {"temperature": 25.0})
    assert render(hass, template)[0]["icon"] == "lametric:20"


async def test_icon_stays_fixed_without_rules(hass: HomeAssistant) -> None:
    """A mode with nothing to follow does nothing: the app keeps its icon."""
    hass.states.async_set("sensor.outside", "5.6")
    hass.states.async_set("sensor.battery", "80")
    for options in (
        by_state(icon_states={}),
        by_value(icon_steps=[]),
        by_state(icon_mode="fixed"),
    ):
        template = build_app_template(options, "awtrix3")
        assert icon_mode(options) is None
        assert render(hass, template)[0]["icon"] == "lametric:9"


async def test_an_older_table_still_follows_the_state(hass: HomeAssistant) -> None:
    """A table saved before `icon_mode` existed is a table by state."""
    old = by_state()
    del old["icon_mode"]
    assert icon_mode(old) == "state"
    hass.states.async_set("sensor.outside", "5.6")
    hass.states.async_set(WEATHER, "snowy")
    assert render(hass, build_app_template(old, "awtrix3"))[0]["icon"] == "lametric:3"


def test_every_icon_of_an_app() -> None:
    """The fixed icon, the table's and the steps' are all got ready."""
    assert icon_names(by_state()) == [
        "lametric:9",
        "lametric:1",
        "lametric:2",
        "lametric:3",
    ]
    assert icon_names(by_value()) == [
        "lametric:9",
        "lametric:10",
        "lametric:20",
        "lametric:50",
        "lametric:95",
    ]
    assert icon_names({"icon": "lametric:9"}) == ["lametric:9"]


def entry() -> MockConfigEntry:
    """A clock to validate against."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="clock",
        data={"firmware": "awtrix3", "prefix": "clock"},
    )


def normalized(hass: HomeAssistant, **options: Any) -> dict[str, Any]:
    """The saved form of a simple app."""
    clock = entry()
    clock.add_to_hass(hass)
    return normalize_app(
        hass, clock, {"mode": "simple", "app_name": "Temp", **options}, None
    )


async def test_validation_keeps_a_table(hass: HomeAssistant) -> None:
    """The entity and the table are kept; empty icons are not."""
    data = normalized(hass, **by_state(icon_states={**ICONS, "fog": ""}))
    assert data["icon_mode"] == "state"
    assert data["icon_entity_id"] == WEATHER
    assert data["icon_states"] == ICONS
    assert "icon_steps" not in data


async def test_validation_sorts_the_steps(hass: HomeAssistant) -> None:
    """Steps come back from the lowest up, numbers tidy, empty icons dropped."""
    data = normalized(
        hass,
        **by_value(
            icon_steps=[
                {"min": "95", "icon": "lametric:95"},
                {"min": 20.0, "icon": "lametric:20"},
                {"min": 0.5, "icon": "lametric:1"},
                {"min": 50, "icon": ""},
            ]
        ),
    )
    assert data["icon_mode"] == "value"
    assert data["icon_steps"] == [
        {"min": 0.5, "icon": "lametric:1"},
        {"min": 20, "icon": "lametric:20"},
        {"min": 95, "icon": "lametric:95"},
    ]
    assert "icon_entity_id" not in data
    assert "icon_states" not in data


async def test_validation_drops_what_a_mode_does_not_use(hass: HomeAssistant) -> None:
    """A fixed icon keeps nothing; a mode keeps only its own rules."""
    fixed = normalized(hass, **by_state(icon_mode="fixed"))
    assert not {"icon_mode", "icon_entity_id", "icon_states", "icon_steps"} & set(fixed)
    state = normalized(hass, **by_state(icon_steps=STEPS))
    assert "icon_steps" not in state
    nothing = normalized(hass, **by_value(icon_steps=[]))
    assert "icon_mode" not in nothing


async def test_validation_migrates_an_older_table(hass: HomeAssistant) -> None:
    """A table saved before `icon_mode` existed becomes a table by state."""
    old = by_state()
    del old["icon_mode"]
    assert normalized(hass, **old)["icon_mode"] == "state"


@pytest.mark.parametrize(
    ("extra", "field", "key"),
    [
        ({"icon_mode": "weather"}, "icon_mode", "invalid_value"),
        ({"icon_entity_id": "nope"}, "icon_entity_id", "invalid_entity"),
        ({"icon_states": ["sunny"]}, "icon_states", "invalid_value"),
        ({"icon_states": {"": "lametric:1"}}, "icon_states", "invalid_value"),
        (
            {"icon_states": {f"s{n}": "x" for n in range(41)}},
            "icon_states",
            "invalid_value",
        ),
        ({"icon": ""}, "icon", "icon_default_required"),
    ],
)
async def test_validation_refuses_a_table(
    hass: HomeAssistant, extra: dict[str, Any], field: str, key: str
) -> None:
    """Bad rules are refused, with the field to blame."""
    with pytest.raises(Invalid) as err:
        normalized(hass, **by_state(**extra))
    assert (err.value.field, err.value.key) == (field, key)


@pytest.mark.parametrize(
    ("extra", "field", "key"),
    [
        ({"icon_steps": "20"}, "icon_steps", "invalid_value"),
        ({"icon_steps": ["x"]}, "icon_steps", "invalid_value"),
        ({"icon_steps": [{"min": "a", "icon": "x"}]}, "icon_steps", "invalid_value"),
        ({"icon_steps": [{"icon": "x"}]}, "icon_steps", "invalid_value"),
        (
            {"icon_steps": [{"min": 1, "icon": "x"}, {"min": 1.0, "icon": "y"}]},
            "icon_steps",
            "invalid_value",
        ),
        (
            {"icon_steps": [{"min": n, "icon": "x"} for n in range(21)]},
            "icon_steps",
            "invalid_value",
        ),
        ({"icon": ""}, "icon", "icon_default_required"),
    ],
)
async def test_validation_refuses_steps(
    hass: HomeAssistant, extra: dict[str, Any], field: str, key: str
) -> None:
    """Bad steps are refused, with the field to blame."""
    with pytest.raises(Invalid) as err:
        normalized(hass, **by_value(**extra))
    assert (err.value.field, err.value.key) == (field, key)


async def test_icons_are_got_ready(hass: HomeAssistant) -> None:
    """Preparing icons downloads each gallery icon once, animated ones included."""
    store = IconStore(hass)
    got: list[int] = []

    async def fake_get(lametric_id: int) -> dict[str, Any]:
        got.append(lametric_id)
        return {"jpeg": "x", "gif": "AAAA"}

    store.async_get = fake_get  # type: ignore[method-assign]

    class Files:
        def __init__(self) -> None:
            self.stored: list[str] = []

        async def async_has(self, name: str) -> bool:
            return False

        async def async_ensure(self, name: str, data: bytes, extension: str) -> bool:
            self.stored.append(f"{name}.{extension}")
            return True

    files = Files()
    await store.async_prepare(
        ["lametric:1", "lametric:2", "not an icon"],
        files,  # type: ignore[arg-type]
    )
    assert got == [1, 2]
    assert files.stored == ["1.gif", "2.gif"]
    await store.async_prepare(["lametric:3"], None)
    assert got == [1, 2, 3]


def test_the_engine_gets_the_icons_to_prepare() -> None:
    """An app that follows an entity lists its icons; one inline for a layout."""

    def config(firmware: str, **extra: Any) -> Any:
        data = {"mode": "simple", "app_name": "Temp", **by_value(**extra)}
        sub = ConfigSubentry(
            data=data, subentry_type="app", title="Temp", unique_id=None
        )  # type: ignore[arg-type]
        return app_config_from_subentry(sub, firmware)

    plain = config("awtrix3")
    assert plain.icons[0] == "lametric:9"
    assert "lametric:95" in plain.icons
    assert plain.icons_inline is False
    assert config("awtrix_ng", layout="value").icons_inline is True
    assert config("awtrix3", icon_mode="fixed").icons == ()


@pytest.mark.parametrize("firmware", ["awtrix3", "awtrix_ng"])
async def test_a_zero_attribute_is_a_value(hass: HomeAssistant, firmware: str) -> None:
    """0 (a battery at 0 %, 0 °C) reaches the "from 0" step, not the default."""
    template = build_app_template(
        by_value(entity_id=WEATHER, attribute="temperature"), firmware
    )
    hass.states.async_set(WEATHER, "rainy", {"temperature": 0})
    assert render(hass, template)[0]["icon"] == "lametric:10"
    hass.states.async_set(WEATHER, "rainy", {"temperature": 0.0})
    assert render(hass, template)[0]["icon"] == "lametric:10"
    hass.states.async_set(WEATHER, "rainy", {"temperature": -3})
    assert render(hass, template)[0]["icon"] == "lametric:9"


async def test_an_attribute_that_is_not_text_matches_the_table(
    hass: HomeAssistant,
) -> None:
    """A number or a boolean attribute is matched as text, like a state."""
    options = {
        "entity_id": "sensor.fan",
        "attribute": "speed",
        "icon": "lametric:9",
        "icon_mode": "state",
        "icon_states": {"0": "lametric:1", "3": "lametric:3", "True": "lametric:4"},
    }
    template = build_app_template(options, "awtrix3")
    for speed, icon in [(0, "lametric:1"), (3, "lametric:3"), (True, "lametric:4")]:
        hass.states.async_set("sensor.fan", "on", {"speed": speed})
        assert render(hass, template)[0]["icon"] == icon, speed
    hass.states.async_set("sensor.fan", "on", {"speed": 7})
    assert render(hass, template)[0]["icon"] == "lametric:9"
