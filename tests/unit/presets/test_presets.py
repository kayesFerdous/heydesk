from typing import Any

import pytest
import yaml

from heydesk.errors import InvalidConfig
from heydesk.presets import load_presets
from heydesk.services.agents import config_from_preset, validate
from heydesk.tools import TOOLS
from tests.conftest import SEED


def seed_config(**changes: Any) -> dict[str, Any]:
    seed = yaml.safe_load(SEED.read_text(encoding="utf-8")) | changes
    return config_from_preset(load_presets()["restaurant"], seed)


def test_every_preset_loads_and_its_tools_are_built() -> None:
    presets = load_presets()
    assert {"restaurant", "receptionist"} <= set(presets)
    for preset in presets.values():
        assert set(preset.tools) <= set(TOOLS), preset.id


def test_the_receptionist_seed_validates() -> None:
    seed = yaml.safe_load((SEED.parent / "receptionist.yaml").read_text(encoding="utf-8"))
    seed.pop("items")
    config = validate(config_from_preset(load_presets()["receptionist"], seed), load_presets())
    assert config.business_name == "Rahman Accounting"
    assert "submit_request" in config.tools


def test_the_example_seed_validates_with_the_preset_defaults() -> None:
    config = validate(seed_config(), load_presets())
    assert config.business_name == "Spice Garden"
    assert config.instructions.startswith("You are the host for {business_name}")
    assert "place_order" in config.tools  # every preset tool is on by default
    assert config.handoff.phone == "+880 1700 000000"


def test_render_is_compact_and_deterministic() -> None:
    config = validate(seed_config(), load_presets())
    preset = load_presets()["restaurant"]
    text = preset.render(config.knowledge, config.currency)

    assert text == preset.render(config.knowledge, config.currency)
    assert "Opening hours: Mon 11:00–22:00, Tue 11:00–22:00, Wed 11:00–22:00, " in text
    assert "Fri 14:00–23:00, Sat 11:00–23:00, Sun 11:00–23:00" in text
    assert "Biryani" not in text  # the menu is items, rendered on their own


def test_problems_come_back_per_field() -> None:
    knowledge = seed_config()["knowledge"]
    knowledge["hours"] = {"mon": "closed"}
    data = seed_config(knowledge=knowledge) | {"tools": ["create_booking"]}

    with pytest.raises(InvalidConfig) as caught:
        validate(data, load_presets())
    assert set(caught.value.fields) == {"knowledge.hours.mon", "tools.create_booking"}
