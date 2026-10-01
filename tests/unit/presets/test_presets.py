from typing import Any

import pytest
import yaml

from heydesk.errors import InvalidConfig
from heydesk.presets import load_presets
from heydesk.services.agents import config_from_preset, validate
from tests.conftest import SEED


def seed_config(**changes: Any) -> dict[str, Any]:
    seed = yaml.safe_load(SEED.read_text(encoding="utf-8")) | changes
    return config_from_preset(load_presets()["restaurant"], seed)


def test_every_preset_loads() -> None:
    assert "restaurant" in load_presets()


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
    assert (
        "- Chicken Biryani [chk-biryani] ৳450 (popular, spicy): "
        "Basmati rice slow-cooked with chicken, potato and egg." in text
    )
    assert "- Mango Lassi [mango-lassi] ৳150 (vegetarian) NOT AVAILABLE" in text
    assert text.index("Rice:") < text.index("Curry:") < text.index("Drinks:")


def test_problems_come_back_per_field() -> None:
    menu = [{"id": "x", "name": "X", "price": -1, "category": "Rice"}]
    knowledge = seed_config()["knowledge"] | {"menu": menu}
    data = seed_config(knowledge=knowledge) | {"tools": ["create_booking"]}

    with pytest.raises(InvalidConfig) as caught:
        validate(data, load_presets())
    assert set(caught.value.fields) == {"knowledge.menu.0.price", "tools.create_booking"}
