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
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

import heydesk.models  # noqa: F401  (registers the tables)
from heydesk.adapters.decider.fake import FakeDecider
from heydesk.adapters.llm.fake import FakeLLM
from heydesk.config import Settings
from heydesk.db import Base, make_engine
from heydesk.domain.values import Confirmed, NewOrder, NewRequest
from heydesk.main import create_app
from heydesk.presets import load_jobs
from heydesk.runtime.config import AgentConfig, Handoff
from heydesk.runtime.items import Item
from heydesk.runtime.tool import ToolContext
from heydesk.services import Services
from heydesk.services.agents import DEV_WORKSPACE_ID
from heydesk.wiring import build_services

FIXED_NOW = datetime(2026, 9, 29, 8, 0, tzinfo=UTC)  # Tuesday, 14:00 in Dhaka
SEED = Path(__file__).parents[1] / "examples" / "agents" / "restaurant.yaml"
DEV_KEY = "dev-restaurant"
MENU = [Item.model_validate(i) for i in yaml.safe_load(SEED.read_text(encoding="utf-8"))["items"]]
AGENT = AgentConfig(  # a small config for unit tests
    preset="restaurant",
    name="Test assistant",
    business_name="Spice Garden",
    timezone="Asia/Dhaka",
    greeting="Hi, welcome to {business_name}!",
    instructions="Be brief.",
    knowledge={},
    tools=[],
    handoff=Handoff(message="Please call us.", phone="+880 1700 000000"),
)
TASKS = {job: load_jobs()[job] for job in ("order", "message")}
CART = {  # what update_order saves for two biryanis and a borhani
    "lines": [
        {"code": "chk-biryani", "name": "Chicken Biryani", "quantity": 2, "price": 45000},
        {"code": "borhani", "name": "Borhani", "quantity": 1, "price": 13000},
    ],
    "total": 103000,
}


class ListCatalog:
    """An in-memory `Catalog` for unit tests: the seed's menu."""

    def __init__(self, items: list[Item] = MENU) -> None:
        self.items = items

    async def get(self, codes: list[str]) -> dict[str, Item]:
        return {i.code: i for i in self.items if i.code in codes}

    async def all(self) -> list[Item]:
        return self.items


class ListOrders:
    """An in-memory `Orders` for unit tests: what was placed."""

    def __init__(self) -> None:
        self.placed: list[NewOrder] = []

    async def place(self, order: Confirmed[NewOrder]) -> int:
        self.placed.append(order.value)
        return 100 + len(self.placed)


class ListRequests:
    """An in-memory `Requests` for unit tests: what was sent."""

    def __init__(self) -> None:
        self.sent: list[NewRequest] = []

    async def submit(self, request: Confirmed[NewRequest]) -> str:
        self.sent.append(request.value)
        return f"req-{len(self.sent)}"


def make_ctx(state: dict[str, Any] | None = None, **kw: Any) -> ToolContext:
    """A `ToolContext` for unit tests; pass any field to change it."""
    fields: dict[str, Any] = {
        "agent": AGENT,
        "state": state or {},
        "items": ListCatalog(),
        "tasks": TASKS,
        "now": FIXED_NOW,
        "decider": FakeDecider(),
        "orders": ListOrders(),
        "requests": ListRequests(),
        "agent_said": "",
        "visitor_said": "",
    }
    return ToolContext(**(fields | kw))


def make_test_settings() -> Settings:
    return Settings(  # offline: never a real provider
        APP_ENV="test", DATABASE_URL="sqlite+aiosqlite://", LLM_PROVIDER="fake", _env_file=None
    )  # type: ignore[call-arg]


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


@pytest.fixture
async def world() -> AsyncIterator[World]:
    engine, sessions = make_engine("sqlite+aiosqlite://")
    await create_tables(engine)
    llm, decider, clock = FakeLLM(), FakeDecider(), Clock()
    services = build_services(sessions, llm, decider, make_test_settings(), clock)
    await services.agents.ensure_dev_agent("restaurant", SEED)
    yield World(services, sessions, llm, decider, clock)
    await engine.dispose()


@pytest.fixture
async def app() -> AsyncIterator[FastAPI]:
    app = create_app(make_test_settings(), llm=FakeLLM(), decider=FakeDecider(), now=Clock())
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
