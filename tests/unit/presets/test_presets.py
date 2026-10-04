from typing import Any

import pytest
import yaml

from heydesk.errors import InvalidConfig
from heydesk.presets import load_jobs, load_presets
from heydesk.services.agents import config_from_preset, validate
from heydesk.tools import TOOLS, tools_for
from tests.conftest import SEED


def seed_config(**changes: Any) -> dict[str, Any]:
    seed = yaml.safe_load(SEED.read_text(encoding="utf-8")) | changes
    return config_from_preset(load_presets()["restaurant"], seed)


def test_every_preset_loads_and_its_jobs_and_their_tools_exist() -> None:
    presets, jobs = load_presets(), load_jobs()
    assert {"restaurant", "receptionist", "clinic"} <= set(presets)
    assert set(jobs) == {"order", "appointment", "message"}
    for preset in presets.values():
        assert set(preset.jobs) <= set(jobs), preset.id
    for job in jobs.values():
        assert job.finish_with in TOOLS and job.description


def test_the_tools_follow_from_the_jobs() -> None:
    jobs = load_jobs()

    def names(*ids: str, search: bool = False) -> list[str]:
        return [t.name for t in tools_for({i: jobs[i] for i in ids}, search=search)]

    assert names("order", "message") == [
        "update_order",
        "set_details",
        "place_order",
        "submit_request",
        "handoff_to_human",
    ]
    assert names("appointment") == ["set_details", "submit_request", "handoff_to_human"]
    assert names("message", search=True)[0] == "search_items"
    assert names() == ["handoff_to_human"]  # a person is always one step away


def test_the_receptionist_seed_validates() -> None:
    seed = yaml.safe_load((SEED.parent / "receptionist.yaml").read_text(encoding="utf-8"))
    seed.pop("items")
    config = validate(config_from_preset(load_presets()["receptionist"], seed), load_presets())
    assert config.business_name == "Rahman Accounting"
    assert config.jobs == ["message"]


def test_the_example_seed_validates_with_the_preset_defaults() -> None:
    config = validate(seed_config(), load_presets())
    assert config.business_name == "Spice Garden"
    assert config.instructions.startswith("You are the host for {business_name}")
    assert config.jobs == ["order", "message"]  # the preset's jobs are on by default
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
    data = seed_config(knowledge=knowledge) | {"jobs": ["time_travel"]}

    with pytest.raises(InvalidConfig) as caught:
        validate(data, load_presets())
    assert set(caught.value.fields) == {"knowledge.hours.mon", "jobs.time_travel"}
