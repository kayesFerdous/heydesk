import pytest
from sqlalchemy import select

from heydesk.errors import NotFound
from heydesk.models import Conversation
from heydesk.ports.llm import LLMUnavailable, ToolCall
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


async def test_a_hand_off_marks_the_conversation_for_a_person(world: World) -> None:
    world.llm.script = [ToolCall("call_1", "handoff_to_human", {}), "Of course.", "Fine."]
    asked = await say(world, "can I talk to a person?")
    fine = await say(world, "hi")
    world.llm.script = [LLMUnavailable("503")]
    failed = await say(world, "hello")

    async with world.sessions() as s:
        status = {c.id: c.status for c in await s.scalars(select(Conversation))}
    assert status == {asked: "needs_human", fine: "active", failed: "needs_human"}


async def test_an_unknown_conversation_is_not_found(world: World) -> None:
    with pytest.raises(NotFound):
        await start(world, "hi", "nope")


async def test_an_order_is_added_up_and_kept_in_the_conversation_state(world: World) -> None:
    lines = [{"code": "chk-biryani", "quantity": 2}, {"code": "borhani", "quantity": 1}]
    world.llm.script = [ToolCall("call_1", "update_order", {"lines": lines}), "That's ৳1,030."]
    conv_id = await say(world, "two biryanis and a borhani")

    async with world.sessions() as s:
        conv = await s.get(Conversation, conv_id)
    assert conv is not None and conv.state["cart"]["total"] == 103000
    assert '"total": "৳1,030"' in conv.messages[3]["content"]  # the tool result the model read
    # The menu is short, so it's in the prompt and search_items isn't offered
    request = world.llm.requests[0]
    assert "Chicken Biryani [chk-biryani] ৳450" in request.system
    assert [t.name for t in request.tools] == [
        "update_order",
        "set_details",
        "place_order",
        "submit_request",
        "handoff_to_human",
    ]
    # After the order, the state note says what to ask next
    assert "Task: pickup order" not in request.system
    assert "Next: ask for the pickup time." in world.llm.requests[1].system


async def test_items_are_searched_in_the_database(world: World) -> None:
    agent = await world.services.agents.by_key(DEV_KEY)
    catalog = world.services.items.catalog(DEV_WORKSPACE_ID, agent.agent_id)

    # Borhani's description says "the classic with biryani"
    assert [i.code for i in await catalog.search("biryanis")] == ["chk-biryani", "borhani"]
    vegetarian = await catalog.search("Vegetarian")  # matches the tags too
    assert len(vegetarian) == 6 and "chk-biryani" not in {i.code for i in vegetarian}
    assert await catalog.search("pizza") == []
    other = world.services.items.catalog("someone-else", agent.agent_id)
    assert await other.get(["chk-biryani"]) == {}  # rule 6: workspace_id on every query
