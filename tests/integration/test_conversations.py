import pytest

from heydesk.errors import NotFound
from heydesk.models import Conversation
from heydesk.services.agents import DEV_WORKSPACE_ID
from heydesk.services.conversations import RunningTurn
from tests.conftest import DEV_KEY, World


async def start(world: World, text: str, conversation_id: str | None = None) -> RunningTurn:
    agent = await world.services.agents.by_key(DEV_KEY)
    return await world.services.conversations.start_turn(agent, conversation_id, text, "web")


async def say(world: World, text: str, conversation_id: str | None = None) -> str:
    """One whole turn, read to the end. Returns the conversation id."""
    turn = await start(world, text, conversation_id)
    [_ async for _ in turn.events]
    return turn.conversation_id


async def test_the_transcript_grows_turn_by_turn(world: World) -> None:
    world.llm.script = ["Biryani is our favourite.", "Two it is."]
    conv_id = await say(world, "What's good?")
    await say(world, "Two of those, please", conv_id)

    async with world.sessions() as s:
        conv = await s.get(Conversation, conv_id)
    assert conv is not None and conv.workspace_id == DEV_WORKSPACE_ID
    assert [(m["role"], m["content"][:12]) for m in conv.messages] == [
        ("assistant", "Hi, welcome "),  # the greeting the visitor saw (D8)
        ("user", "What's good?"),
        ("assistant", "Biryani is o"),
        ("user", "Two of those"),
        ("assistant", "Two it is."),
    ]
    # The second model call saw everything before it
    assert world.llm.requests[1].messages == conv.messages[:-1]


async def test_the_prompt_has_the_date_and_an_edit_shows_up_next_message(world: World) -> None:
    conv_id = await say(world, "hi")
    agent = await world.services.agents.by_key(DEV_KEY)
    await world.services.agents.update(DEV_WORKSPACE_ID, agent.agent_id, {"faq": "Changed."})

    await say(world, "and parking?", conv_id)

    first, second = world.llm.requests
    assert "Now: Tuesday 29 September 2026, 14:00" in first.system
    assert "Changed." not in first.system and "Changed." in second.system


async def test_an_unknown_conversation_is_not_found(world: World) -> None:
    with pytest.raises(NotFound):
        await start(world, "hi", "nope")
