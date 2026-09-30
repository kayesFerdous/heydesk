from collections.abc import AsyncIterator

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from heydesk.adapters.decider.fake import FakeDecider
from heydesk.adapters.llm.fake import FakeLLM
from heydesk.config import Settings
from heydesk.main import create_app


def make_test_settings() -> Settings:
    return Settings(APP_ENV="test", DATABASE_URL="sqlite+aiosqlite://", _env_file=None)  # type: ignore[call-arg]


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    app = create_app(make_test_settings(), llm=FakeLLM(), decider=FakeDecider())
    async with (
        LifespanManager(app),
        AsyncClient(transport=ASGITransport(app), base_url="http://test") as c,
    ):
        yield c
