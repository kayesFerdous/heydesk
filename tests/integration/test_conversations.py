import pytest
from sqlalchemy import select

from heydesk.errors import NotFound
from heydesk.models import Conversation, Turn
from heydesk.ports.llm import LLMUnavailable, ToolCall
from heydesk.services.agents import DEV_WORKSPACE_ID
from heydesk.services.conversations import RunningTurn
from heydesk.services.team import HUMAN
from heydesk.shop.store import ShopStore
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
    world.llm.script = ["The kurti is our favourite.", "Two it is."]
    conv_id = await say(world, "What's good?")
    await say(world, "Two of those, please", conv_id)

    async with world.sessions() as s:
        conv = await s.get(Conversation, conv_id)
    assert conv is not None and conv.workspace_id == DEV_WORKSPACE_ID
    assert [(m["role"], m["content"][:12]) for m in conv.messages] == [
        ("assistant", "Assalamu ala"),  # the greeting the visitor saw
        ("user", "What's good?"),
        ("assistant", "The kurti is"),
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
    kurti = {"code": "kurti", "quantity": 2, "size": "m", "colour": "black"}
    world.llm.script = [ToolCall("call_1", "update_cart", {"lines": [kurti]}), "That's ৳1,900."]
    conv_id = await say(world, "two black kurtis, size M")

    async with world.sessions() as s:
        conv = await s.get(Conversation, conv_id)
    assert conv is not None and conv.state["cart"]["subtotal"] == 190000
    assert "৳1,900" in conv.messages[3]["content"]  # the tool result the model read
    # The products are in the prompt; the order so far is added once there is one
    request = world.llm.requests[0]
    assert "Cotton Kurti [kurti]" in request.system
    assert [t.name for t in request.tools] == [
        "find_product",
        "update_cart",
        "place_order",
        "track_order",  # after the order
        "change_order",
        "cancel_order",
        "confirm_order",
        "request_exchange",
        "show_photos",
        "take_message",
        "take_lead",
        "handoff_to_human",
        "flag_complaint",
        "off_topic",
        "remember",
    ]
    assert "# The order so far" not in request.system
    assert "# The order so far\n2 × Cotton Kurti" in world.llm.requests[1].system


async def test_a_store_only_sees_its_own_workspace(world: World) -> None:
    agent = await world.services.agents.by_key(DEV_KEY)
    mine = world.store(agent)
    other = ShopStore(world.sessions, "someone-else", world.clock)

    assert len(await mine.products()) == 6
    assert await other.products() == []  # rule 6: workspace_id on every query


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
        conv = await s.get(Conversation, conv_id)
        conv.status, conv.taken_over_at = HUMAN, world.clock()  # the owner took the chat over

    await say(world, "are you there?", conv_id)

    assert len(await turns(world)) == 1


async def test_a_chat_taken_over_and_left_quiet_is_the_agents_again(world: World) -> None:
    """The inbox's switch, or ".": the agent is quiet; 30 minutes after the team last wrote,
    the next message is answered."""
    agent = await world.agent()
    conv = await world.say(agent, None, ["Hello!"], "hi")
    await world.services.conversations.take_over(agent.workspace_id, conv, True)
    await world.say(agent, conv, ["Should not be said."], "anyone?")
    assert "Should not be said." not in str((await world.conversation(conv)).messages)
    world.clock.advance(minutes=31)
    await world.say(agent, conv, ["Back again."], "hello?")
    saved = await world.conversation(conv)
    assert "Back again." in str(saved.messages) and saved.status == "active"
