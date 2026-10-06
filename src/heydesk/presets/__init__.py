"""Agent presets: a YAML file and a knowledge model each (design §5). Pure data."""

import importlib
from collections.abc import Callable
from functools import cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, field_validator

from heydesk.runtime.tasks import Task

HERE = Path(__file__).parent


class Preset(BaseModel):
    id: str
    label: str
    description: str
    greeting: str
    instructions: str
    items_label: str = "Items"  # what the prompt calls the business's items: "Menu", "Services"
    jobs: list[str]  # what this kind of business does, from jobs.yaml; fixed, not the owner's (D37)
    tools: list[str] = []  # this kind's own tools, on top of what its jobs need: `find_doctor`
    knowledge_model: type[BaseModel]  # "module:Class" in the YAML
    render_knowledge: Callable[[Any, str], str]  # "module:function": (knowledge, currency) → text

    @field_validator("knowledge_model", "render_knowledge", mode="before")
    @classmethod
    def _resolve(cls, value: object) -> object:
        if isinstance(value, str):
            module, _, name = value.partition(":")
            return getattr(importlib.import_module(module), name)
        return value

    def render(self, knowledge: dict[str, Any], currency: str) -> str:
        """Knowledge as prompt text. Raises pydantic's ValidationError if it doesn't fit the model."""
        return self.render_knowledge(self.knowledge_model.model_validate(knowledge), currency)


@cache
def load_jobs() -> dict[str, Task]:
    """The jobs library: every job a preset can name (D32, D37)."""
    data = yaml.safe_load((HERE / "jobs.yaml").read_text(encoding="utf-8"))
    return {job_id: Task.model_validate(job) for job_id, job in data.items()}


@cache
def load_presets() -> dict[str, Preset]:
    presets: dict[str, Preset] = {}
    for path in sorted(HERE.glob("*/preset.yaml")):
        preset = Preset.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
        unknown = set(preset.jobs) - set(load_jobs())
        if unknown:
            raise ValueError(f"preset {preset.id} names jobs that don't exist: {sorted(unknown)}")
        presets[preset.id] = preset
    return presets
