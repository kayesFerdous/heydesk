"""A whole order through the conversation service: saved only after a read-back and a yes."""

from sqlalchemy import select

from heydesk.models import Conversation, Record
from heydesk.ports.llm import ToolCall
from heydesk.services.agents import DEV_WORKSPACE_ID
from tests.conftest import DEV_KEY, World

LINES = [{"code": "chk-biryani", "quantity": 2}, {"code": "borhani", "quantity": 1}]
DETAILS = {"pickup_time": "7:30 pm", "name": "Kayes", "phone": "01712345678"}
READ_BACK = "2 Chicken Biryani and 1 Borhani, ৳1,030, at 7:30 pm for Kayes. Shall I place it?"


async def say(world: World, text: str, conversation_id: str | None = None) -> str:
    agent = await world.services.agents.by_key(DEV_KEY)
    turn = await world.services.conversations.start_turn(agent, conversation_id, text, "web")
    [_ async for _ in turn.events]
    return turn.conversation_id


async def order_then(world: World, reply: str) -> str:
    """The visitor orders, the agent reads it back, the visitor replies; the model then tries
    place_order. Returns the conversation id."""
    world.llm.script = [
        ToolCall("c1", "update_order", {"lines": LINES}),
        ToolCall("c2", "set_details", {"task": "order", "details": DETAILS}),
        READ_BACK,
        ToolCall("c3", "place_order", {}),
        "Done!",
    ]
    conv_id = await say(world, "two biryanis and a borhani for 7:30, I'm Kayes, 01712345678")
    await say(world, reply, conv_id)
    return conv_id


async def orders(world: World) -> list[Record]:
    async with world.sessions() as s:
        return list(await s.scalars(select(Record).where(Record.kind == "order")))


async def test_a_yes_saves_one_order(world: World) -> None:
    world.decider.script = [{"read_back": 0.95, "said_yes": 0.95}]
    conv_id = await order_then(world, "yes")

    [order] = await orders(world)
    assert (order.workspace_id, order.conversation_id) == (DEV_WORKSPACE_ID, conv_id)
    assert (order.job, order.number, order.total, order.customer_name) == (
        "order",
        101,
        103000,
        "Kayes",
    )
    # Jev saw the read-back and the yes
    [asked] = world.decider.asked
    assert (asked["agent_last_message"], asked["visitor_reply"]) == (READ_BACK, "yes")
    async with world.sessions() as s:
        conv = await s.get(Conversation, conv_id)
    assert conv is not None and conv.state["cart"] == {}  # the task is done
    assert '"order_number": 101' in conv.messages[-2]["content"]


async def test_hmm_maybe_saves_nothing(world: World) -> None:
    world.decider.script = [{"read_back": 0.95, "said_yes": 0.3}]
    conv_id = await order_then(world, "hmm, maybe")

    assert await orders(world) == []
    async with world.sessions() as s:
        conv = await s.get(Conversation, conv_id)
    assert conv is not None and conv.state["cart"]["total"] == 103000  # still there to confirm
    assert "clear yes" in conv.messages[-2]["content"]


async def test_a_whole_order_is_saved_only_at_the_yes(world: World) -> None:
    """Four turns: the order, the details, a "maybe", then a yes."""
    world.decider.script = [
        {"read_back": 0.95, "said_yes": 0.3},
        {"read_back": 0.95, "said_yes": 0.95},
    ]
    turns = [
        ("two biryanis and a borhani", [ToolCall("c1", "update_order", {"lines": LINES})]),
        (
            "7:30 pm, Kayes, 01712345678",
            [ToolCall("c2", "set_details", {"task": "order", "details": DETAILS})],
        ),
        ("hmm, maybe", [ToolCall("c3", "place_order", {})]),
        ("yes", [ToolCall("c4", "place_order", {})]),
    ]
    conv_id = None
    for n, (text, calls) in enumerate(turns):
        world.llm.script = [*calls, READ_BACK if n < 3 else "Done! Your order number is 101."]
        conv_id = await say(world, text, conv_id)
        assert len(await orders(world)) == (1 if n == 3 else 0)

    [order] = await orders(world)
    assert (order.total, order.customer_phone, order.conversation_id) == (
        103000,
        "01712345678",
        conv_id,
    )
