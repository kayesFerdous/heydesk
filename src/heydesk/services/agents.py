"""The agent: a business has one. Create it, edit it (design §5).
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
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import ColumnElement, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from heydesk.errors import Conflict, InvalidConfig, NotFound, Unavailable
from heydesk.models import Agent, Workspace
from heydesk.services.billing import dev_workspace
from heydesk.services.knowledge import KnowledgeService
from heydesk.services.photos import PhotoService
from heydesk.shop.config import ShopConfig
from heydesk.shop.products import Product
from heydesk.shop.prompt import shop_missing
from heydesk.shop.store import ShopStore

log = logging.getLogger(__name__)

DEV_WORKSPACE_ID = "dev"


@dataclass(frozen=True)
class LiveAgent:
    """What a channel needs to talk to an agent."""

    workspace_id: str
    agent_id: str
    public_key: str
    config: ShopConfig
    documents: list[str]  # the titles of its documents; any means `search_knowledge`

    @property
    def greeting(self) -> str:
        return self.config.fill(self.config.greeting)


@dataclass(frozen=True)
class AgentInfo:
    """An agent as its business sees it."""

    id: str
    public_key: str
    config: ShopConfig
    missing: list[str]  # what the owner still has to fill in, in plain words


def validate(data: dict[str, Any]) -> ShopConfig:
    """Runs on every save. Problems come back per field."""
    try:
        return ShopConfig.model_validate(data)
    except ValidationError as error:
        raise InvalidConfig(
            {".".join(str(p) for p in e["loc"]): e["msg"] for e in error.errors()}
        ) from None


class AgentService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        knowledge: KnowledgeService,
        photos: PhotoService,
        now: Callable[[], datetime],
        public_url: str = "",
    ) -> None:
        self.sessions = sessions
        self.knowledge = knowledge
        self.photos = photos
        self.now = now
        self.public_url = public_url  # where a store's links start: the payment and order pages

    async def create(
        self, workspace_id: str, fields: dict[str, Any], *, public_key: str | None = None
    ) -> str:
        """The business's agent. A business has only one."""
        async with self.sessions() as s:
            business = await s.get(Workspace, workspace_id)
        name = business.name if business else workspace_id
        # A business can start with no fields: the name comes from signup
        config = validate({"business_name": name, "name": f"{name} assistant", **fields})
        now = self.now()
        agent = Agent(
            workspace_id=workspace_id,
            name=config.name,
            public_key=public_key or secrets.token_urlsafe(12),
            config=config.model_dump(mode="json", exclude_unset=True),
            created_at=now,
            updated_at=now,
        )
        async with self.sessions() as s, s.begin():
            if await s.scalar(select(Agent.id).where(Agent.workspace_id == workspace_id)):
                raise Conflict("This business already has its agent. Edit it instead.")
            s.add(agent)
        return agent.id

    async def update(self, workspace_id: str, patch: dict[str, Any]) -> ShopConfig:
        """Top-level fields in `patch` replace the config's. Live from the next message."""
        async with self.sessions() as s, s.begin():
            agent = await self._get(s, workspace_id)
            config = validate({**agent.config, **patch})
            agent.config = config.model_dump(
                mode="json", exclude_unset=True
            )  # a new dict, so SQLAlchemy sees it
            agent.name = config.name
            agent.updated_at = self.now()
        return config

    async def get(self, workspace_id: str) -> AgentInfo:
        async with self.sessions() as s:
            agent = await self._get(s, workspace_id)
        config = ShopConfig.model_validate(agent.config)
        store = self.build_store(workspace_id)
        missing = await shop_missing(config, store)
        if config.pay_first and not await store.pays_first(config):
            missing.append("Connect SSLCommerz or bKash to take payments first.")
        return AgentInfo(agent.id, agent.public_key, config, missing)

    async def store(self, workspace_id: str) -> ShopStore:
        """The business's data, for its owner's routes. 404 until it has an agent."""
        async with self.sessions() as s:
            await self._get(s, workspace_id)
        return self.build_store(workspace_id)

    def build_store(self, workspace_id: str, conversation_id: str | None = None) -> ShopStore:
        """The business's data, always built the same way, so no store is missing a part:
        with the documents' search, the photo importer and the address its links start with.
        A turn's tools get one for their `conversation_id`, so what they save is linked to it."""
        return ShopStore(
            self.sessions,
            workspace_id,
            self.now,
            conversation_id=conversation_id,
            knowledge=self.knowledge,
            public_url=self.public_url,
            photos=self.photos,
        )

    async def by_key(self, key: str) -> LiveAgent:
        return await self._live(Agent.public_key == key)

    async def by_workspace(self, workspace_id: str) -> LiveAgent:
        """The business's agent, for a service that acts for its owner."""
        return await self._live(Agent.workspace_id == workspace_id)

    async def by_id(self, agent_id: str) -> LiveAgent:
        """For a messaging app, which knows its agent by id."""
        return await self._live(Agent.id == agent_id)

    async def _live(self, where: ColumnElement[bool]) -> LiveAgent:
        async with self.sessions() as s:
            agent = await s.scalar(select(Agent).where(where))
            business = await s.get(Workspace, agent.workspace_id) if agent else None
        if agent is None or business is None:
            raise NotFound("There's no agent with that key.")
        if business.status != "active":
            raise Unavailable("This assistant isn't available right now.")
        return LiveAgent(
            agent.workspace_id,
            agent.id,
            agent.public_key,
            ShopConfig.model_validate(agent.config),
            await self.knowledge.titles(agent.id),
        )

    async def ensure_dev_agent(self, seed_path: str | Path) -> LiveAgent:
        """The `.env` agent (design §5): the dev workspace's one agent, from its seed file,
        updated from the seed on every start (its products too). Its key is `dev-shop`."""
        text = await asyncio.to_thread(Path(seed_path).read_text, encoding="utf-8")
        seed: dict[str, Any] = yaml.safe_load(text)
        key = "dev-shop"
        async with self.sessions() as s, s.begin():
            if await s.get(Workspace, DEV_WORKSPACE_ID) is None:
                s.add(dev_workspace(DEV_WORKSPACE_ID, self.now()))
            agent = await s.scalar(select(Agent).where(Agent.workspace_id == DEV_WORKSPACE_ID))
            if agent is not None:
                agent.public_key = key
        store = self.build_store(DEV_WORKSPACE_ID)
        products = seed.pop("products", [])  # the rest of the seed is the config
        await store.replace_products(TypeAdapter(list[Product]).validate_python(products))
        if agent is None:
            await self.create(DEV_WORKSPACE_ID, seed, public_key=key)
        else:
            async with self.sessions() as s, s.begin():
                saved = await self._get(s, DEV_WORKSPACE_ID)
                saved.config = validate(seed).model_dump(mode="json", exclude_unset=True)
                saved.name = str(saved.config["name"])
        log.info("dev agent ready", extra={"public_key": key})
        return await self.by_key(key)

    async def _get(self, s: AsyncSession, workspace_id: str) -> Agent:
        agent = await s.scalar(select(Agent).where(Agent.workspace_id == workspace_id))
        if agent is None:
            raise NotFound("This business has no agent yet. Create one first.")
        return agent
