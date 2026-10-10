"""The agent: a business has one, of one kind (its desk). Create it, edit it (design §5).
Edits are live from the next message."""

import asyncio
import logging
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError
from sqlalchemy import ColumnElement, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from heydesk.desks import DESKS
from heydesk.desks.base import Desk, Store
from heydesk.errors import Conflict, InvalidConfig, NotFound, Unavailable
from heydesk.models import Agent, Workspace
from heydesk.runtime.config import AgentConfig
from heydesk.services.knowledge import KnowledgeService

log = logging.getLogger(__name__)

DEV_WORKSPACE_ID = "dev"


@dataclass(frozen=True)
class LiveAgent:
    """What a channel needs to talk to an agent."""

    workspace_id: str
    agent_id: str
    public_key: str
    desk: Desk
    config: AgentConfig  # the desk's own config class
    documents: list[str]  # the titles of its documents; any means `search_knowledge` (D35)

    @property
    def greeting(self) -> str:
        return self.config.fill(self.config.greeting)


@dataclass(frozen=True)
class AgentInfo:
    """An agent as its business sees it."""

    id: str
    public_key: str
    kind: str
    config: AgentConfig
    missing: list[str]  # what the owner still has to fill in, in plain words


def desk_of(kind: str) -> Desk:
    if kind not in DESKS:
        raise InvalidConfig({"kind": f"there's no {kind!r} desk; use one of {', '.join(DESKS)}"})
    return DESKS[kind]


def validate(desk: Desk, data: dict[str, Any]) -> AgentConfig:
    """Runs on every save. Problems come back per field."""
    try:
        return desk.config_model.model_validate(data)
    except ValidationError as error:
        raise InvalidConfig(
            {".".join(str(p) for p in e["loc"]): e["msg"] for e in error.errors()}
        ) from None


class AgentService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        knowledge: KnowledgeService,
        now: Callable[[], datetime],
    ) -> None:
        self.sessions = sessions
        self.knowledge = knowledge
        self.now = now

    async def create(
        self, ws: str, kind: str, fields: dict[str, Any], *, public_key: str | None = None
    ) -> str:
        """The business's agent. A business has only one (D22)."""
        desk = desk_of(kind)
        async with self.sessions() as s:
            business = await s.get(Workspace, ws)
        name = business.name if business else ws
        # A business starts from just its kind: the name comes from signup (D26)
        config = validate(desk, {"business_name": name, "name": f"{name} assistant", **fields})
        now = self.now()
        agent = Agent(
            workspace_id=ws,
            kind=kind,
            name=config.name,
            public_key=public_key or secrets.token_urlsafe(12),
            config=config.model_dump(mode="json", exclude_unset=True),
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
            config = validate(DESKS[agent.kind], {**agent.config, **patch})
            agent.config = config.model_dump(
                mode="json", exclude_unset=True
            )  # a new dict, so SQLAlchemy sees it
            agent.name = config.name
            agent.updated_at = self.now()
        return config

    async def get(self, ws: str) -> AgentInfo:
        async with self.sessions() as s:
            agent = await self._get(s, ws)
        desk = DESKS[agent.kind]
        config = desk.config_model.model_validate(agent.config)
        store = desk.store(self.sessions, ws, self.now)
        missing = await desk.missing(config, store)
        if getattr(config, "pay_first", False) and not await store.pays_first(config):
            missing.append("Connect your SSLCommerz account to take payments first.")
        return AgentInfo(agent.id, agent.public_key, agent.kind, config, missing)

    async def store(self, ws: str, kind: str | None = None) -> Store:
        """The business's data, for its owner's routes. With a `kind`, 404 unless its agent is
        of that kind (a restaurant has no doctors)."""
        async with self.sessions() as s:
            agent = await self._get(s, ws)
        if kind and agent.kind != kind:
            raise NotFound(f"This business's agent isn't a {kind}.")
        return DESKS[agent.kind].store(self.sessions, ws, self.now)

    async def by_key(self, key: str) -> LiveAgent:
        return await self._live(Agent.public_key == key)

    async def by_id(self, agent_id: str) -> LiveAgent:
        """For a messaging app, which knows its agent by id (D42)."""
        return await self._live(Agent.id == agent_id)

    async def _live(self, where: ColumnElement[bool]) -> LiveAgent:
        async with self.sessions() as s:
            agent = await s.scalar(select(Agent).where(where))
            business = await s.get(Workspace, agent.workspace_id) if agent else None
        if agent is None or business is None:
            raise NotFound("There's no agent with that key.")
        if business.status != "active":
            raise Unavailable("This assistant isn't available right now.")
        desk = DESKS[agent.kind]
        return LiveAgent(
            agent.workspace_id,
            agent.id,
            agent.public_key,
            desk,
            desk.config_model.model_validate(agent.config),
            await self.knowledge.titles(agent.id),
        )

    async def ensure_dev_agent(self, kind: str, seed_path: str | Path) -> LiveAgent:
        """The `.env` agent (design §5): the dev workspace's one agent, from its seed file,
        updated from the seed on every start (its menu or doctors too). Its key is `dev-<kind>`."""
        desk = desk_of(kind)
        text = await asyncio.to_thread(Path(seed_path).read_text, encoding="utf-8")
        seed: dict[str, Any] = yaml.safe_load(text)
        key = f"dev-{kind}"
        async with self.sessions() as s, s.begin():
            if await s.get(Workspace, DEV_WORKSPACE_ID) is None:
                s.add(Workspace(id=DEV_WORKSPACE_ID, name="Dev", created_at=self.now()))
            agent = await s.scalar(select(Agent).where(Agent.workspace_id == DEV_WORKSPACE_ID))
            if agent is not None:  # a changed DEV_AGENT_KIND keeps the one agent
                agent.public_key = key
                agent.kind = kind
        store = desk.store(self.sessions, DEV_WORKSPACE_ID, self.now)
        await desk.seed(store, seed)  # takes its own lists out of `seed`
        if agent is None:
            await self.create(DEV_WORKSPACE_ID, kind, seed, public_key=key)
        else:
            async with self.sessions() as s, s.begin():
                saved = await self._get(s, DEV_WORKSPACE_ID)
                saved.config = validate(desk, seed).model_dump(mode="json", exclude_unset=True)
                saved.name = str(saved.config["name"])
        log.info("dev agent ready", extra={"public_key": key})
        return await self.by_key(key)

    async def _get(self, s: AsyncSession, ws: str) -> Agent:
        agent = await s.scalar(select(Agent).where(Agent.workspace_id == ws))
        if agent is None:
            raise NotFound("This business has no agent yet. Create one first.")
        return agent
