"""The agent: a business has one. Create it from a preset, and edit it (design §5). Edits are live at once."""

import asyncio
import logging
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from heydesk.errors import Conflict, InvalidConfig, NotFound
from heydesk.models import Agent, Workspace
from heydesk.presets import Preset
from heydesk.runtime.config import AgentConfig
from heydesk.runtime.items import Item, render_items
from heydesk.runtime.tasks import Task
from heydesk.services.items import ItemService

log = logging.getLogger(__name__)

DEV_WORKSPACE_ID = "dev"


@dataclass(frozen=True)
class LiveAgent:
    """What a channel needs to talk to an agent."""

    workspace_id: str
    agent_id: str
    public_key: str
    config: AgentConfig
    tasks: dict[str, Task]  # from the preset
    knowledge: str  # rendered for the prompt, items included
    items_in_prompt: bool  # every item is listed, so `search_items` isn't needed (D17)

    @property
    def greeting(self) -> str:
        return self.config.fill(self.config.greeting)


@dataclass(frozen=True)
class AgentInfo:
    """An agent as its business sees it."""

    id: str
    public_key: str
    config: AgentConfig


def config_from_preset(preset: Preset, overrides: dict[str, Any]) -> dict[str, Any]:
    """The preset's defaults under the business's own fields."""
    return {
        "greeting": preset.greeting,
        "instructions": preset.instructions,
        "tools": preset.tools,
        **overrides,
        "preset": preset.id,
    }


def validate(data: dict[str, Any], presets: dict[str, Preset]) -> AgentConfig:
    """Runs on every save. Problems come back per field."""
    try:
        config = AgentConfig.model_validate(data)
    except ValidationError as error:
        raise InvalidConfig(_fields(error)) from None
    preset = presets.get(config.preset)
    if preset is None:
        raise InvalidConfig({"preset": f"there's no {config.preset!r} preset"})
    problems: dict[str, str] = {}
    try:
        preset.knowledge_model.model_validate(config.knowledge)
    except ValidationError as error:
        problems |= _fields(error, "knowledge")
    for name in config.tools:
        if name not in preset.tools:
            problems[f"tools.{name}"] = f"not available to the {preset.id} preset"
    if problems:
        raise InvalidConfig(problems)
    return config


def _info(agent: Agent) -> AgentInfo:
    return AgentInfo(agent.id, agent.public_key, AgentConfig.model_validate(agent.config))


def _fields(error: ValidationError, prefix: str = "") -> dict[str, str]:
    return {
        ".".join(str(part) for part in (prefix, *e["loc"]) if part != ""): e["msg"]
        for e in error.errors()
    }


class AgentService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        presets: dict[str, Preset],
        items: ItemService,
        max_items_in_prompt: int,
        now: Callable[[], datetime],
    ) -> None:
        self.sessions = sessions
        self.presets = presets
        self.items = items
        self.max_items_in_prompt = max_items_in_prompt
        self.now = now

    def _preset(self, preset_id: str) -> Preset:
        if preset_id not in self.presets:
            raise InvalidConfig({"preset": f"there's no {preset_id!r} preset"})
        return self.presets[preset_id]

    async def create_from_preset(
        self, ws: str, preset_id: str, overrides: dict[str, Any], *, public_key: str | None = None
    ) -> str:
        """The business's agent. A business has only one (D22)."""
        config = validate(config_from_preset(self._preset(preset_id), overrides), self.presets)
        now = self.now()
        agent = Agent(
            workspace_id=ws,
            preset=config.preset,
            name=config.name,
            public_key=public_key or secrets.token_urlsafe(12),
            config=config.model_dump(mode="json"),
            created_at=now,
            updated_at=now,
        )
        async with self.sessions() as s, s.begin():
            if await s.scalar(select(Agent.id).where(Agent.workspace_id == ws)):
                raise Conflict("This business already has its agent. Edit it instead.")
            s.add(agent)
        return agent.id

    async def update(self, ws: str, patch: dict[str, Any]) -> AgentConfig:
        """Top-level fields in `patch` replace the config's. Live from the next message."""
        async with self.sessions() as s, s.begin():
            agent = await self._get(s, ws)
            config = validate({**agent.config, **patch}, self.presets)
            agent.config = config.model_dump(mode="json")  # a new dict, so SQLAlchemy sees it
            agent.name = config.name
            agent.updated_at = self.now()
        return config

    async def get(self, ws: str) -> AgentInfo:
        async with self.sessions() as s:
            return _info(await self._get(s, ws))

    async def by_key(self, key: str) -> LiveAgent:
        async with self.sessions() as s:
            agent = await s.scalar(select(Agent).where(Agent.public_key == key))
        if agent is None:
            raise NotFound("There's no agent with that key.")
        config = AgentConfig.model_validate(agent.config)
        preset = self.presets[config.preset]
        items = await self.items.all(agent.workspace_id, agent.id)
        in_prompt = len(items) <= self.max_items_in_prompt
        knowledge = "\n\n".join(
            [
                preset.render(config.knowledge, config.currency),
                render_items(items, preset.items_label, config.currency, self.max_items_in_prompt),
            ]
        )
        return LiveAgent(
            agent.workspace_id,
            agent.id,
            agent.public_key,
            config,
            preset.tasks,
            knowledge,
            in_prompt,
        )

    async def ensure_dev_agent(self, preset_id: str, seed_path: str | Path) -> LiveAgent:
        """The `.env` agent (design §5): the dev workspace's one agent, from the preset + seed,
        updated from the seed on every start (its items too). Its key is `dev-<preset>`."""
        text = await asyncio.to_thread(Path(seed_path).read_text, encoding="utf-8")
        seed: dict[str, Any] = yaml.safe_load(text)
        try:
            items = TypeAdapter(list[Item]).validate_python(seed.pop("items", []))
        except ValidationError as error:
            raise InvalidConfig(_fields(error, "items")) from None
        key = f"dev-{preset_id}"
        async with self.sessions() as s, s.begin():
            if await s.get(Workspace, DEV_WORKSPACE_ID) is None:
                s.add(Workspace(id=DEV_WORKSPACE_ID, name="Dev", created_at=self.now()))
            agent = await s.scalar(select(Agent).where(Agent.workspace_id == DEV_WORKSPACE_ID))
            if agent is not None:
                agent.public_key = key  # a changed DEV_AGENT_PRESET keeps the one agent
            agent_id = agent.id if agent else None
        if agent_id is None:
            agent_id = await self.create_from_preset(
                DEV_WORKSPACE_ID, preset_id, seed, public_key=key
            )
        else:
            data = config_from_preset(self._preset(preset_id), seed)
            await self.update(DEV_WORKSPACE_ID, data)
        await self.items.replace(DEV_WORKSPACE_ID, agent_id, items)
        log.info("dev agent ready", extra={"public_key": key})
        return await self.by_key(key)

    async def _get(self, s: AsyncSession, ws: str) -> Agent:
        agent = await s.scalar(select(Agent).where(Agent.workspace_id == ws))
        if agent is None:
            raise NotFound("This business has no agent yet. Create one from a preset.")
        return agent
