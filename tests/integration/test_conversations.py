import pytest
from sqlalchemy import select

from heydesk.errors import NotFound
from heydesk.models import Conversation, Turn
from heydesk.ports.llm import LLMUnavailable, ToolCall
from heydesk.services.agents import DEV_WORKSPACE_ID
from heydesk.services.conversations import RunningTurn
from heydesk.services.team import HUMAN
from tests.conftest import DEV_KEY, World


async def start(world: World, text: str, conversation_id: str | None = None) -> RunningTurn:
    agent = await world.services.agents.by_key(DEV_KEY)
    return await world.services.conversations.start_turn(
        agent, conversation_id, text, "web", "web:test-visitor"
    )


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
    await world.services.agents.update(DEV_WORKSPACE_ID, {"faq": "Changed."})

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
    # The menu is in the prompt; the order so far is added once there is one
    request = world.llm.requests[0]
    assert "Chicken Biryani [chk-biryani] ৳450" in request.system
    assert [t.name for t in request.tools] == [
        "update_order",
        "place_order",
        "track_order",  # after the order (D65)
        "change_order",
        "cancel_order",
        "book_table",  # the seed takes bookings (D64)
        "cancel_booking",
        "take_message",
        "take_lead",
        "handoff_to_human",
        "flag_complaint",
        "off_topic",
        "remember",
    ]
    assert "# The order so far" not in request.system
    assert "# The order so far\n2 × Chicken Biryani" in world.llm.requests[1].system


async def test_a_store_only_sees_its_own_workspace(world: World) -> None:
    agent = await world.services.agents.by_key(DEV_KEY)
    mine = world.store(agent)
    other = agent.desk.store(world.sessions, "someone-else", world.clock)

    assert len(await mine.menu()) == 10
    assert await other.menu() == []  # rule 6: workspace_id on every query


async def turns(world: World) -> list[Turn]:
    async with world.sessions() as s:
        return list(await s.scalars(select(Turn).order_by(Turn.at)))


async def test_each_turn_is_saved_with_its_tokens_and_tools_but_no_words(world: World) -> None:
    world.llm.script = [ToolCall("call_1", "handoff_to_human", {}), "Someone will call you."]
    conv_id = await say(world, "can I talk to a person?")

    [turn] = await turns(world)
    assert (turn.workspace_id, turn.conversation_id) == (DEV_WORKSPACE_ID, conv_id)
    assert (turn.channel, turn.llm, turn.tools) == ("web", "fake", ["handoff_to_human"])
    assert (turn.input_tokens, turn.output_tokens) == (0, 5)  # the fake counts words out
    assert turn.handed_off and not turn.error and not turn.interrupted
    assert turn.total_ms >= 0 and turn.first_output_ms is not None
    row = {c.key: getattr(turn, c.key) for c in Turn.__table__.columns}
    assert "person" not in str(row) and "call you" not in str(row)  # nobody's words


async def test_a_failed_turn_keeps_its_error(world: World) -> None:
    world.llm.script = [LLMUnavailable("503 from the provider")]
    await say(world, "hello")

    [turn] = await turns(world)
    assert turn.error == "503 from the provider" and turn.handed_off


async def test_a_message_to_the_team_is_not_a_turn(world: World) -> None:
    world.llm.script = ["Hello!"]
    conv_id = await say(world, "hi")
    async with world.sessions() as s, s.begin():
        (await s.get(Conversation, conv_id)).status = HUMAN  # the owner took the chat over

    await say(world, "are you there?", conv_id)

    assert len(await turns(world)) == 1


async def test_a_chat_taken_over_and_left_quiet_is_the_agents_again(world: World) -> None:
    """The inbox's switch, or ".": the agent is quiet; 30 minutes after the team last wrote,
    the next message is answered (D135)."""
    agent = await world.agent()
    conv = await world.say(agent, None, ["Hello!"], "hi")
    await world.services.conversations.take_over(agent.workspace_id, conv, True)
    await world.say(agent, conv, ["Should not be said."], "anyone?")
    assert "Should not be said." not in str((await world.conversation(conv)).messages)
    world.clock.advance(minutes=31)
    await world.say(agent, conv, ["Back again."], "hello?")
    saved = await world.conversation(conv)
    assert "Back again." in str(saved.messages) and saved.status == "active"
