"""The limits on a public chat: off-topic strikes, a block, and the daily turns."""

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from heydesk.errors import VisitorLimited
from heydesk.ports.llm import ToolCall
from heydesk.runtime.events import Token
from tests.conftest import DEV_KEY, World

JOKE = ToolCall("c", "off_topic", {"reply": "I can only help with Spice Garden's food here."})


async def say(world: World, script: list[object], visitor: str = "web:a") -> str:
    """One turn from `visitor`; returns what the visitor saw."""
    agent = await world.services.agents.by_key(DEV_KEY)
    world.llm.script = script
    turn = await world.services.conversations.start_turn(agent, None, "…", "web", visitor)
    return "".join([e.text async for e in turn.events if isinstance(e, Token)])


async def test_off_topic_says_the_models_line_with_one_model_call(world: World) -> None:
    said = await say(world, [JOKE])

    assert said == "I can only help with Spice Garden's food here."
    assert len(world.llm.requests) == 1  # the tool's line ended the turn


async def test_five_off_topic_turns_in_a_row_block_the_visitor_for_a_day(world: World) -> None:
    for _ in range(5):
        await say(world, [JOKE])
    calls = len(world.llm.requests)

    with pytest.raises(VisitorLimited, match=r"\+880"):  # how to reach the business instead
        await say(world, ["never said"])
    assert len(world.llm.requests) == calls  # a blocked visitor costs no model call
    assert await say(world, ["Hello!"], visitor="web:b") == "Hello!"  # others are fine

    world.clock.advance(hours=25)
    assert await say(world, ["Welcome back."]) == "Welcome back."


async def test_a_real_step_clears_the_strikes(world: World) -> None:
    for _ in range(4):
        await say(world, [JOKE])
    await say(world, [ToolCall("h", "handoff_to_human", {}), "Of course."])
    for _ in range(4):
        await say(world, [JOKE])

    assert await say(world, ["Still here."]) == "Still here."


async def test_a_visitor_gets_a_set_number_of_turns_a_day(world: World) -> None:
    world.services.visitors.settings.VISITOR_DAILY_TURNS = 2
    await say(world, ["one"])
    await say(world, ["two"])

    with pytest.raises(VisitorLimited):
        await say(world, ["three"])

    world.clock.advance(days=1)
    assert await say(world, ["a new day"]) == "a new day"


async def test_a_business_gets_a_set_number_of_turns_a_day(world: World) -> None:
    world.services.visitors.settings.WORKSPACE_DAILY_TURNS = 2
    await say(world, ["one"], visitor="web:a")
    await say(world, ["two"], visitor="web:b")

    with pytest.raises(VisitorLimited):
        await say(world, ["three"], visitor="web:c")


async def test_the_chat_api_needs_a_visitor_and_says_429_when_limited(
    client: AsyncClient, app: FastAPI
) -> None:
    url = f"/api/chat/{DEV_KEY}"
    assert (await client.post(url, json={"message": "hi"})).status_code == 422

    app.state.services.visitors.settings.VISITOR_DAILY_TURNS = 1
    body = {"visitor_id": "browser-token-1", "message": "hi"}
    assert (await client.post(url, json=body)).status_code == 200
    limited = await client.post(url, json=body)

    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "visitor_limited"
