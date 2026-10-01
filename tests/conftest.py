from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

import heydesk.models  # noqa: F401  (registers the tables)
from heydesk.adapters.decider.fake import FakeDecider
from heydesk.adapters.llm.fake import FakeLLM
from heydesk.config import Settings
from heydesk.db import Base, make_engine
from heydesk.main import create_app
from heydesk.services import Services
from heydesk.wiring import build_services

FIXED_NOW = datetime(2026, 9, 29, 8, 0, tzinfo=UTC)  # Tuesday, 14:00 in Dhaka
SEED = Path(__file__).parents[1] / "examples" / "agents" / "restaurant.yaml"
DEV_KEY = "dev-restaurant"


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
    clock: Clock


@pytest.fixture
async def world() -> AsyncIterator[World]:
    engine, sessions = make_engine("sqlite+aiosqlite://")
    await create_tables(engine)
    llm, clock = FakeLLM(), Clock()
    services = build_services(sessions, llm, FakeDecider(), make_test_settings(), clock)
    await services.agents.ensure_dev_agent("restaurant", SEED)
    yield World(services, sessions, llm, clock)
    await engine.dispose()


@pytest.fixture
async def app() -> AsyncIterator[FastAPI]:
    app = create_app(make_test_settings(), llm=FakeLLM(), decider=FakeDecider(), now=Clock())
    async with LifespanManager(app):
        await create_tables(app.state.engine)
        await app.state.services.agents.ensure_dev_agent("restaurant", SEED)
        yield app


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as c:
        yield c
