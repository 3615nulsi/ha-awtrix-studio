"""Tests for notifications: the messages they build and when they are sent."""

from __future__ import annotations

from datetime import timedelta
import json

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.awtrix_studio.const import NOTIFICATION_TEST_DURATION
from custom_components.awtrix_studio.drivers import Awtrix3Driver, AwtrixNGDriver
from custom_components.awtrix_studio.notifications import (
    NotificationConfig,
    NotificationRule,
    config_from_data,
    is_trigger,
    normalize_notification,
    payload_from_template,
    wrap_page,
)
from custom_components.awtrix_studio.templates import build_lines_template
from custom_components.awtrix_studio.validation import Invalid

ENTITY = "binary_sensor.doorbell"


class FakePublisher:
    """Record what the notification publishes."""

    def __init__(self, error: str | None = None) -> None:
        """Start with nothing published."""
        self.messages: list[tuple[str, str]] = []
        self.error = error

    async def __call__(self, topic: str, payload: str) -> str | None:
        """Record one publication."""
        self.messages.append((topic, payload))
        return self.error


def make_config(**overrides: object) -> NotificationConfig:
    """Build a simple notification with sensible defaults."""
    settings: dict[str, object] = {
        "name": "doorbell",
        "trigger_entity_id": ENTITY,
        "trigger_state": "on",
        "dismiss_on_clear": True,
        "mode": "simple",
        "content": {
            "mode": "simple",
            "entity_id": ENTITY,
            "label": "Door",
            "color": [255, 140, 0],
        },
        "duration": 10,
    }
    settings.update(overrides)
    return NotificationConfig(**settings)  # type: ignore[arg-type]


PAGE = {"text": "Hi", "color": "#FF8C00"}


def test_wrap_page_keeps_the_layout_of_the_page() -> None:
    """What an app would draw (a layout, say) is kept as it is."""
    page = {"layout": {"version": 1, "regions": []}}
    payload = wrap_page(make_config(), page)
    assert payload["layout"] == page["layout"]


def test_wrap_page_awtrix_ng() -> None:
    """AWTRIX NG gets only the documented keys, named, with its own units."""
    payload = wrap_page(make_config(repeat=2, sound="bell"), PAGE)
    assert payload == {
        "text": "Hi",
        "color": "#FF8C00",
        "name": "doorbell",
        "hold": False,
        "stack": True,
        "durationMs": 10000,
        "repeat": 2,
        "sound": "bell",
    }
    assert "repeat" not in wrap_page(make_config(), PAGE)


def test_template_payload_gets_the_name() -> None:
    """A template's JSON is sent as it is, but the name to dismiss it by is ours."""
    config = make_config(mode="template")
    assert payload_from_template(config, {"text": "x"}) == {
        "text": "x",
        "name": "doorbell",
    }
    # A name the template gives itself would not be found by the dismissal.
    assert (
        payload_from_template(config, {"text": "x", "name": "mine"})["name"]
        == "doorbell"
    )


def test_lines_are_a_text_and_or_an_entity() -> None:
    """A line without an entity shows its text; the layout places each line."""
    text_line = {"entity_id": "", "label": "Power cut", "color": [255, 0, 0]}
    entity_line = {"entity_id": "sensor.ups", "label": "UPS", "font": "matrix-light6"}
    template = build_lines_template(
        {**text_line, "layout": "two_lines", "segments": [entity_line]}
    )
    assert '"layout"' in template
    assert 'states("sensor.ups")' in template
    assert '"font": "matrix-light6"' in template
    assert "Power cut" in template
    # Centered is the clock's default: only a left or right text says so.
    assert '"align"' not in template
    right = build_lines_template(
        {**text_line, "align": "end", "layout": "line", "segments": []}
    )
    assert '"align": "end"' in right
    # Without a layout, one text is sent.
    classic = build_lines_template({**text_line, "segments": []})
    assert "states(" not in classic
    assert '"layout"' not in classic


def test_is_trigger() -> None:
    """The state is compared without case or spaces, and None never matches."""
    assert is_trigger("ON", "on")
    assert is_trigger(" on ", "On")
    assert not is_trigger("off", "on")
    assert not is_trigger(None, "on")


def test_config_from_stored_data() -> None:
    """A stored notification becomes a config, with defaults for what is missing."""
    config = config_from_data(
        {
            "notification_name": "n",
            "trigger_entity_id": ENTITY,
            "mode": "simple",
            "entity_id": "sensor.ups",
            "label": "UPS",
        }
    )
    assert config.trigger_entity_id == ENTITY
    assert config.content == {
        "mode": "simple",
        "entity_id": "sensor.ups",
        "label": "UPS",
    }
    assert config.trigger_state == "on"
    assert config.dismiss_on_clear is True
    assert config.duration == 10


async def test_sent_when_the_state_is_entered(hass: HomeAssistant) -> None:
    """Entering the state sends once; staying in it, or other states, do not."""
    hass.states.async_set(ENTITY, "off")
    publisher = FakePublisher()
    rule = NotificationRule(hass, AwtrixNGDriver("ng"), make_config(), publisher)
    await rule.async_enable()

    hass.states.async_set(ENTITY, "on")
    await hass.async_block_till_done()
    assert [topic for topic, _ in publisher.messages] == ["ng/cmd/notify"]
    assert "Door" in json.loads(publisher.messages[0][1])["text"]
    assert rule.active
    assert rule.last_sent is not None

    hass.states.async_set(ENTITY, "on", {"changed": "attribute only"})
    await hass.async_block_till_done()
    assert len(publisher.messages) == 1  # it did not re-enter the state

    await rule.async_shutdown()


@pytest.mark.parametrize("before", [None, "unavailable", "unknown"])
async def test_not_sent_when_the_old_state_was_not_known(
    hass: HomeAssistant, before: str | None
) -> None:
    """An entity that is on as Home Assistant starts, or as its integration
    reloads, is not an entity that just switched on."""
    if before is not None:
        hass.states.async_set(ENTITY, before)
    publisher = FakePublisher()
    rule = NotificationRule(
        hass, AwtrixNGDriver("ng"), make_config(dismiss_on_clear=False), publisher
    )
    await rule.async_enable()

    hass.states.async_set(ENTITY, "on")
    await hass.async_block_till_done()
    assert publisher.messages == []
    assert not rule.active

    # Once it is known, a real change shows the notification.
    hass.states.async_set(ENTITY, "off")
    hass.states.async_set(ENTITY, "on")
    await hass.async_block_till_done()
    assert [topic for topic, _ in publisher.messages] == ["ng/cmd/notify"]
    await rule.async_shutdown()


async def test_dismissed_when_the_state_ends(hass: HomeAssistant) -> None:
    """Leaving the state takes the notification off, when asked to."""
    hass.states.async_set(ENTITY, "off")
    publisher = FakePublisher()
    rule = NotificationRule(hass, AwtrixNGDriver("ng"), make_config(), publisher)
    await rule.async_enable()

    hass.states.async_set(ENTITY, "on")
    await hass.async_block_till_done()
    hass.states.async_set(ENTITY, "off")
    await hass.async_block_till_done()
    assert publisher.messages[-1] == ("ng/cmd/notify/dismiss/doorbell", "")
    assert not rule.active
    await rule.async_shutdown()


async def test_not_dismissed_when_not_asked(hass: HomeAssistant) -> None:
    """Without the option, the notification runs its course."""
    hass.states.async_set(ENTITY, "off")
    publisher = FakePublisher()
    rule = NotificationRule(
        hass, AwtrixNGDriver("ng"), make_config(dismiss_on_clear=False), publisher
    )
    await rule.async_enable()
    hass.states.async_set(ENTITY, "on")
    await hass.async_block_till_done()
    hass.states.async_set(ENTITY, "off")
    await hass.async_block_till_done()
    assert len(publisher.messages) == 1
    await rule.async_shutdown()


async def test_ng_dismisses_by_name(hass: HomeAssistant) -> None:
    """AWTRIX NG has a topic per notification name."""
    hass.states.async_set(ENTITY, "on")
    publisher = FakePublisher()
    rule = NotificationRule(hass, AwtrixNGDriver("ng"), make_config(), publisher)
    await rule.async_enable()
    await rule.async_remove_from_clock()
    assert publisher.messages == [("ng/cmd/notify/dismiss/doorbell", "")]
    await rule.async_shutdown()


async def test_ng_dismisses_when_the_state_ends_after_a_restart(
    hass: HomeAssistant,
) -> None:
    """Nothing was sent since the start, yet NG still takes the notification off."""
    hass.states.async_set(ENTITY, "on")
    publisher = FakePublisher()
    rule = NotificationRule(hass, AwtrixNGDriver("ng"), make_config(), publisher)
    await rule.async_enable()

    hass.states.async_set(ENTITY, "off")
    await hass.async_block_till_done()
    assert publisher.messages == [("ng/cmd/notify/dismiss/doorbell", "")]
    await rule.async_shutdown()


async def test_awtrix3_has_no_notifications(hass: HomeAssistant) -> None:
    """Notifications are for AWTRIX NG: an AWTRIX 3 clock sends and dismisses
    nothing."""
    publisher = FakePublisher()
    rule = NotificationRule(hass, Awtrix3Driver("awtrix"), make_config(), publisher)
    assert await rule.async_send() == "this firmware has no notifications"
    await rule.async_remove_from_clock()
    assert publisher.messages == []


async def test_awtrix3_clock_cannot_have_notifications(hass: HomeAssistant) -> None:
    """The panel and the form refuse a notification on an AWTRIX 3 clock."""
    entry = MockConfigEntry(
        domain="awtrix_studio", data={"firmware": "awtrix3", "prefix": "awtrix"}
    )
    with pytest.raises(Invalid) as err:
        normalize_notification(
            hass, entry, {"notification_name": "door", "mode": "simple"}, None
        )
    assert err.value.key == "notifications_ng_only"


async def test_a_failed_publish_is_reported(hass: HomeAssistant) -> None:
    """The reason is kept so that the panel can show it."""
    hass.states.async_set(ENTITY, "on")
    publisher = FakePublisher(error="too big")
    rule = NotificationRule(hass, AwtrixNGDriver("ng"), make_config(), publisher)
    assert await rule.async_send() == "too big"
    assert rule.last_error == "too big"
    assert not rule.active


async def test_a_bad_template_is_reported(hass: HomeAssistant) -> None:
    """A template that renders no JSON is an error, not a crash."""
    publisher = FakePublisher()
    rule = NotificationRule(
        hass,
        AwtrixNGDriver("ng"),
        make_config(mode="template", template="not json"),
        publisher,
    )
    assert await rule.async_send() is not None
    assert publisher.messages == []


async def test_test_takes_the_notification_off_again(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """A test is dismissed after a few seconds, even when it is held."""
    hass.states.async_set(ENTITY, "on")
    publisher = FakePublisher()
    rule = NotificationRule(
        hass, AwtrixNGDriver("ng"), make_config(hold=True), publisher
    )
    assert await rule.async_test() is None
    assert publisher.messages[-1][0] == "ng/cmd/notify"

    freezer.tick(timedelta(seconds=NOTIFICATION_TEST_DURATION + 1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert publisher.messages[-1] == ("ng/cmd/notify/dismiss/doorbell", "")
    assert not rule.active


async def test_a_new_trigger_waits_for_the_delay(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """Off, on, off, on within the delay shows the notification only once."""
    hass.states.async_set(ENTITY, "off")
    publisher = FakePublisher()
    rule = NotificationRule(
        hass,
        AwtrixNGDriver("ng"),
        make_config(cooldown=60, dismiss_on_clear=False),
        publisher,
    )
    await rule.async_enable()

    for state in ("on", "off", "on"):
        hass.states.async_set(ENTITY, state)
        await hass.async_block_till_done()
    assert len(publisher.messages) == 1

    freezer.tick(timedelta(minutes=61))
    hass.states.async_set(ENTITY, "off")
    await hass.async_block_till_done()
    hass.states.async_set(ENTITY, "on")
    await hass.async_block_till_done()
    assert len(publisher.messages) == 2
    await rule.async_shutdown()
