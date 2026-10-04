"""A message for the team through the conversation service: saved only after a yes."""

from sqlalchemy import select

from heydesk.models import Record
from heydesk.ports.llm import ToolCall
from heydesk.services.agents import DEV_WORKSPACE_ID
from tests.conftest import SEED, World

DETAILS = {"body": "Please call me about my tax return", "name": "Kayes", "phone": "01712345678"}
READ_BACK = "A message from Kayes, 01712345678: please call about the tax return. Shall I send it?"


async def message_then(world: World, reply: str) -> str:
    agent = await world.services.agents.ensure_dev_agent(
        "receptionist", SEED.parent / "receptionist.yaml"
    )
    world.llm.script = [
        ToolCall("c1", "set_details", {"task": "message", "details": DETAILS}),
        READ_BACK,
        ToolCall("c2", "submit_request", {}),
        "Sent!",
    ]
    conversations = world.services.conversations
    turn = await conversations.start_turn(agent, None, "please ask them to call me", "web")
    [_ async for _ in turn.events]
    reply_turn = await conversations.start_turn(agent, turn.conversation_id, reply, "web")
    [_ async for _ in reply_turn.events]
    return turn.conversation_id


async def requests(world: World) -> list[Record]:
    async with world.sessions() as s:
        return list(await s.scalars(select(Record).where(Record.kind == "request")))


async def test_a_yes_saves_the_message(world: World) -> None:
    world.decider.script = [{"read_back": 0.95, "said_yes": 0.95}]
    conv_id = await message_then(world, "yes please")

    [saved] = await requests(world)
    assert (saved.workspace_id, saved.conversation_id) == (DEV_WORKSPACE_ID, conv_id)
    assert (saved.job, saved.customer_name, saved.customer_phone, saved.number) == (
        "message",
        "Kayes",
        "01712345678",
        101,
    )
    assert saved.details == {"body": "Please call me about my tax return"}


async def test_no_yes_saves_nothing(world: World) -> None:
    world.decider.script = [{"read_back": 0.95, "said_yes": 0.2}]
    await message_then(world, "wait, not yet")

    assert await requests(world) == []
