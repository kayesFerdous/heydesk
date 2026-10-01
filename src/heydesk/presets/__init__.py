"""Agent presets: a YAML file and a knowledge model each (design §5). Pure data."""

import importlib
from collections.abc import Callable
from functools import cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, field_validator

HERE = Path(__file__).parent


class Preset(BaseModel):
    id: str
    label: str
    description: str
    greeting: str
    instructions: str
    items_label: str = "Items"  # what the prompt calls the business's items: "Menu", "Services"
    tools: list[str]  # the tools an agent of this kind may use; all on by default
    tasks: dict[str, dict[str, Any]]  # typed as TaskDef with runtime/tasks.py (day 6)
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
def load_presets() -> dict[str, Preset]:
    presets: dict[str, Preset] = {}
    for path in sorted(HERE.glob("*/preset.yaml")):
        preset = Preset.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
        presets[preset.id] = preset
    return presets
