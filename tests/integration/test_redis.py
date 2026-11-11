"""Redis: every server process shares the rate limits and the live inbox.

These need a real Redis at REDIS_URL, so a plain `pytest` leaves them out. CI's `redis` job
runs them: `REDIS_URL=redis://localhost:6379 uv run pytest -m redis`.
"""

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import suppress

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from redis.asyncio import Redis

from heydesk.api.sse import live_frames
from heydesk.errors import TooFast
from heydesk.services.agents import DEV_WORKSPACE_ID
from heydesk.services.live import CHANNEL, LiveHub
from heydesk.services.rate_limits import RateLimiter
from tests.conftest import DEV_KEY

pytestmark = pytest.mark.redis


@pytest.fixture
async def redis() -> AsyncIterator[Redis]:
    client = Redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379"))
    yield client
    await client.aclose()


async def test_two_processes_add_to_one_count(redis: Redis) -> None:
    first, second = RateLimiter(redis), RateLimiter(redis)  # as two server processes
    visitor = f"browser-{uuid.uuid4()}"  # a fresh count, whatever an earlier run left
    ip = f"ip-{uuid.uuid4()}"
    for limiter in (first, second) * 3:  # 6 a minute, half on each process
        await limiter.chat(visitor, ip)

    with pytest.raises(TooFast):
        await first.chat(visitor, ip)


async def test_a_change_on_one_process_reaches_a_page_on_another(redis: Redis) -> None:
    making, showing = LiveHub(redis), LiveHub(redis)
    listening = await start_listening(showing, redis)
    try:
        with showing.listen("workspace-1") as page:
            making.publish("workspace-1", "conversation-1")
            assert await asyncio.wait_for(page.get(), 2) == "conversation-1"
    finally:
        await stop(listening)


async def test_the_inbox_hears_a_turn_through_redis(
    app: FastAPI, client: AsyncClient, redis: Redis
) -> None:
    hub = app.state.services.live
    hub.redis = redis  # as production builds it: the change goes out to Redis and comes back
    listening = await start_listening(hub, redis)
    try:
        app.state.services.conversations.llm.script = ["Hello!"]
        frames = live_frames(hub, DEV_WORKSPACE_ID)
        assert (await anext(frames)).startswith("event: open")

        res = await client.post(
            f"/api/chat/{DEV_KEY}", json={"visitor_id": "test-visitor", "message": "hi"}
        )
        conversation = res.text.split('"conversation_id": "')[1].split('"')[0]
        frame = await asyncio.wait_for(anext(frames), 2)
        assert f'"conversation_id": "{conversation}"' in frame
        await frames.aclose()
    finally:
        await stop(listening)


async def start_listening(hub: LiveHub, redis: Redis) -> asyncio.Task[None]:
    """Starts the hub's `run` and waits until Redis counts it as listening."""
    before = await listeners(redis)
    task = asyncio.create_task(hub.run())
    for _ in range(200):  # up to 2 seconds
        if await listeners(redis) > before:
            return task
        await asyncio.sleep(0.01)
    raise AssertionError("the hub never started listening")


async def listeners(redis: Redis) -> int:
    [(_, count)] = await redis.pubsub_numsub(CHANNEL)
    return int(count)


async def stop(task: asyncio.Task[None]) -> None:
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
