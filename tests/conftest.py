from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from qdrant_client import AsyncQdrantClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

import heydesk.desks  # noqa: F401  (registers every desk's tables)
from heydesk.adapters.decider.fake import FakeDecider
from heydesk.adapters.embedder.fake import FakeEmbedder
from heydesk.adapters.knowledge.qdrant import QdrantKnowledgeStore
from heydesk.adapters.llm.fake import FakeLLM
from heydesk.adapters.telegram.fake import FakeTelegram
from heydesk.config import Settings
from heydesk.db import Base, make_engine
from heydesk.desks.restaurant import RestaurantConfig
from heydesk.desks.restaurant.values import MenuItem
from heydesk.main import create_app
from heydesk.models import Conversation
from heydesk.runtime.config import Handoff
from heydesk.runtime.tool import ToolContext
from heydesk.services import Services
from heydesk.services.agents import DEV_WORKSPACE_ID, LiveAgent
from heydesk.wiring import build_services

FIXED_NOW = datetime(2026, 9, 29, 8, 0, tzinfo=UTC)  # Tuesday, 14:00 in Dhaka
SEEDS = Path(__file__).parents[1] / "examples" / "agents"
SEED = SEEDS / "restaurant.yaml"
DEV_KEY = "dev-restaurant"
AGENT = RestaurantConfig(  # a small config for unit tests
    name="Test assistant",
    business_name="Spice Garden",
    timezone="Asia/Dhaka",
    greeting="Hi, welcome to {business_name}!",
    handoff=Handoff(message="Please call us.", phone="+880 1700 000000"),
)
MENU = [
    MenuItem.model_validate(i) for i in yaml.safe_load(SEED.read_text(encoding="utf-8"))["menu"]
]
CART = {  # what update_order saves for two biryanis and a borhani
    "lines": [
        {"code": "chk-biryani", "name": "Chicken Biryani", "quantity": 2, "price": 45000},
        {"code": "borhani", "name": "Borhani", "quantity": 1, "price": 13000},
    ],
    "total": 103000,
}


class MenuStore:
    """Just enough of a `RestaurantStore` for unit tests: the seed's menu."""

    async def menu(self) -> list[MenuItem]:
        return MENU


def make_ctx(store: Any = None, state: dict[str, Any] | None = None, **kw: Any) -> ToolContext[Any]:
    """A `ToolContext` for unit tests; pass any field to change it."""
    fields: dict[str, Any] = {
        "agent": AGENT,
        "store": store,
        "state": state or {},
        "now": FIXED_NOW,
        "decider": FakeDecider(),
        "agent_said": "",
        "visitor_said": "",
    }
    return ToolContext(**(fields | kw))


def make_test_settings() -> Settings:
    return Settings(  # offline: never a real provider
        APP_ENV="test",
        DATABASE_URL="sqlite+aiosqlite://",
        LLM_PROVIDER="fake",
        EMBEDDER="fake",
        EMBEDDING_DIMENSIONS=64,
        QDRANT_LOCAL_PATH=":memory:",
        PUBLIC_URL="https://heydesk.test",
        PAYMENTS="fake",
        _env_file=None,
    )  # type: ignore[call-arg]


def make_knowledge() -> tuple[FakeEmbedder, QdrantKnowledgeStore]:
    """An offline knowledge base: fake embeddings and Qdrant's own engine in memory."""
    embedder = FakeEmbedder(dimensions=64)
    store = QdrantKnowledgeStore(AsyncQdrantClient(":memory:"), "test", dimensions=64)
    return embedder, store


async def create_tables(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@dataclass
class Clock:
    """A clock a test can move: `clock.advance(seconds=121)`."""

    now: datetime = FIXED_NOW

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


@dataclass
class World:
    """Services on an in-memory database, with the restaurant dev agent."""

    services: Services
    sessions: async_sessionmaker[AsyncSession]
    llm: FakeLLM
    decider: FakeDecider  # says no unless a test scripts it
    clock: Clock
    embedder: FakeEmbedder

    async def agent(self, kind: str = "restaurant") -> LiveAgent:
        """The dev agent as a `kind` of desk, from its seed file."""
        return await self.services.agents.ensure_dev_agent(kind, SEEDS / f"{kind}.yaml")

    def store(self, agent: LiveAgent, conversation_id: str | None = None) -> Any:
        """The agent's desk store, as its tools get it."""
        return agent.desk.store(
            self.sessions, agent.workspace_id, self.clock, conversation_id=conversation_id
        )

    async def say(
        self, agent: LiveAgent, conversation_id: str | None, script: list[Any], text: str = "…"
    ) -> str:
        """One turn with a scripted model; returns the conversation id."""
        self.llm.script = script
        turn = await self.services.conversations.start_turn(agent, conversation_id, text, "web")
        [_ async for _ in turn.events]
        return turn.conversation_id

    async def conversation(self, conversation_id: str) -> Conversation:
        async with self.sessions() as s:
            conv = await s.get(Conversation, conversation_id)
        assert conv is not None
        return conv

    async def tool_said(self, conversation_id: str) -> str:
        """What the last tool returned, as the model saw it."""
        return str((await self.conversation(conversation_id)).messages[-2]["content"])


@pytest.fixture
async def world() -> AsyncIterator[World]:
    engine, sessions = make_engine("sqlite+aiosqlite://")
    await create_tables(engine)
    llm, decider, clock = FakeLLM(), FakeDecider(), Clock()
    embedder, store = make_knowledge()
    services = build_services(sessions, llm, decider, embedder, store, make_test_settings(), clock)
    await services.agents.ensure_dev_agent("restaurant", SEED)
    yield World(services, sessions, llm, decider, clock, embedder)
    await store.client.close()
    await engine.dispose()


@pytest.fixture
async def app() -> AsyncIterator[FastAPI]:
    embedder, store = make_knowledge()
    app = create_app(
        make_test_settings(),
        llm=FakeLLM(),
        decider=FakeDecider(),
        embedder=embedder,
        knowledge_store=store,
        telegram=FakeTelegram(),
        now=Clock(),
    )
    async with LifespanManager(app):
        await create_tables(app.state.engine)
        await app.state.services.agents.ensure_dev_agent("restaurant", SEED)
        yield app


async def new_business(app: FastAPI, email: str = "second@example.com") -> AsyncClient:
    """A client logged in to a new business, which has no agent yet."""
    anon = AsyncClient(transport=ASGITransport(app), base_url="http://test")
    body = {"email": email, "password": "another long one", "workspace_name": "Cafe"}
    signed_up = await anon.post("/api/auth/signup", json=body)
    anon.headers["Authorization"] = f"Bearer {signed_up.json()['token']}"
    return anon


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """Logged in as a user of the dev workspace, which has the restaurant dev agent."""
    token = await app.state.services.users.create_user(
        DEV_WORKSPACE_ID, "owner@example.com", "a long password"
    )
    headers = {"Authorization": f"Bearer {token}"}
    async with AsyncClient(
        transport=ASGITransport(app), base_url="http://test", headers=headers
    ) as c:
        yield c
