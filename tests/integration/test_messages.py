"""Messages for the team: every desk takes them, only after a yes; the receptionist is built on
them alone; the owner sees them in /api/messages."""

from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select

from heydesk.desks import DESKS, tools_for
from heydesk.desks.receptionist import RECEPTIONIST
from heydesk.models import TeamMessage
from heydesk.ports.llm import ToolCall
from tests.conftest import DEV_KEY, World

MESSAGE = {"message": "Do you cater weddings?", "name": "Kayes", "phone": "01712345678"}
YES = {"read_back": 0.95, "said_yes": 0.95}


async def messages(world: World) -> list[TeamMessage]:
    async with world.sessions() as s:
        return list(await s.scalars(select(TeamMessage)))


async def test_every_desk_takes_messages_and_hands_off(world: World) -> None:
    for desk in DESKS.values():
        names = [t.name for t in tools_for(desk, has_documents=False)]
        assert names[-2:] == ["take_message", "handoff_to_human"], desk.kind
        assert "search_knowledge" in [t.name for t in tools_for(desk, has_documents=True)]


async def test_a_message_is_saved_only_after_a_yes(world: World) -> None:
    agent = await world.agent()
    take = ToolCall("c", "take_message", MESSAGE)
    world.decider.script = [{"read_back": 0.1, "said_yes": 0.1}, YES]

    conv = await world.say(agent, None, [take, "Shall I pass that on?"])
    assert (
        "Message: Do you cater weddings?; from Kayes; phone 01712345678"
        in await world.tool_said(conv)
    )
    assert await messages(world) == []

    await world.say(agent, conv, [take, "Done."], "yes")
    [saved] = await messages(world)
    assert (saved.code, saved.body, saved.conversation_id) == ("M-101", MESSAGE["message"], conv)


async def test_the_receptionist_answers_from_its_own_fields(world: World) -> None:
    agent = await world.agent("receptionist")
    text = await RECEPTIONIST.prompt(agent.config, world.store(agent))

    assert "You are the receptionist for {business_name}." in text
    assert "Company registration, ৳40,000" in text
    assert [t.name for t in tools_for(agent.desk, has_documents=False)] == [
        "take_message",
        "handoff_to_human",
    ]


async def test_the_owner_sees_messages_and_marks_them_handled(
    app: FastAPI, client: AsyncClient
) -> None:
    runtime = app.state.services.conversations.runtime
    take = ToolCall("c", "take_message", MESSAGE)
    runtime.llm.script = [take, "Shall I pass that on?", take, "Done."]
    runtime.decider.script = [{"read_back": 0.1, "said_yes": 0.1}, YES]
    first = await client.post(f"/api/chat/{DEV_KEY}", json={"message": "a message please"})
    conv_id = first.text.split('"conversation_id": "')[1].split('"')[0]
    await client.post(f"/api/chat/{DEV_KEY}", json={"message": "yes", "conversation_id": conv_id})

    [message] = (await client.get("/api/messages")).json()
    assert (message["code"], message["name"], message["status"]) == ("M-101", "Kayes", "new")
    assert (await client.get("/api/messages", params={"q": "m-101"})).json() == [message]

    handled = await client.patch(f"/api/messages/{message['id']}", json={"status": "handled"})
    assert handled.json()["status"] == "handled"
    unknown = await client.patch(f"/api/messages/{message['id']}", json={"status": "lost"})
    assert unknown.status_code == 400
