"""Messages for the team: every desk takes them, only after a yes; the receptionist is built on
them alone; the owner sees them in /api/messages."""

from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select

from heydesk.desks import DESKS, tools_for
from heydesk.desks.receptionist import RECEPTIONIST
from heydesk.models import TeamMessage
from heydesk.ports.llm import ToolCall
from heydesk.services.agents import DEV_WORKSPACE_ID
from tests.conftest import DEV_KEY, World

MESSAGE = {"message": "Do you cater weddings?", "name": "Kayes", "phone": "01712345678"}
YES = {"read_back": 0.95, "said_yes": 0.95}


async def messages(world: World) -> list[TeamMessage]:
    async with world.sessions() as s:
        return list(await s.scalars(select(TeamMessage)))


async def test_every_desk_takes_messages_hands_off_and_declines_timepass(world: World) -> None:
    for desk in DESKS.values():
        config = (await world.agent(desk.kind)).config
        names = [t.name for t in tools_for(desk, config, has_documents=False)]
        assert names[-3:] == ["take_message", "handoff_to_human", "off_topic"], desk.kind
        assert "search_knowledge" in [t.name for t in tools_for(desk, config, True)]


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


async def test_a_name_and_phone_are_optional(world: World) -> None:
    agent = await world.agent()
    take = ToolCall("c", "take_message", {"message": "Loved the biryani!"})
    world.decider.script = [{"read_back": 0.1, "said_yes": 0.1}, YES]

    conv = await world.say(agent, None, [take, "Shall I pass that on?"])
    said = await world.tool_said(conv)
    assert '"read_back": "Message: Loved the biryani!"' in said
    assert "ask once whether they'd like to leave a name and phone" in said

    await world.say(agent, conv, [take, "Done."], "yes, no number needed")
    [saved] = await messages(world)
    assert (saved.body, saved.name, saved.phone) == ("Loved the biryani!", "", "")
    assert "can't call back" in await world.tool_said(conv)

    bad = ToolCall("c", "take_message", {"message": "Call me", "phone": "12"})
    conv = await world.say(agent, None, [bad, "What's your number?"])
    assert "doesn't look like a phone number" in await world.tool_said(conv)


async def test_the_receptionist_answers_from_its_own_fields(world: World) -> None:
    agent = await world.agent("receptionist")
    text = await RECEPTIONIST.prompt(agent.config, world.store(agent))

    assert "You are the receptionist for {business_name}." in text
    assert "Company registration, ৳40,000" in text
    assert [t.name for t in tools_for(agent.desk, agent.config, has_documents=False)] == [
        "take_message",
        "handoff_to_human",
        "off_topic",
    ]


async def test_the_owner_sees_messages_and_marks_them_handled(
    app: FastAPI, client: AsyncClient
) -> None:
    runtime = app.state.services.conversations.runtime
    take = ToolCall("c", "take_message", MESSAGE)
    runtime.llm.script = [take, "Shall I pass that on?", take, "Done."]
    runtime.decider.script = [{"read_back": 0.1, "said_yes": 0.1}, YES]
    first = await client.post(
        f"/api/chat/{DEV_KEY}", json={"visitor_id": "test-visitor", "message": "a message please"}
    )
    conv_id = first.text.split('"conversation_id": "')[1].split('"')[0]
    await client.post(
        f"/api/chat/{DEV_KEY}",
        json={"visitor_id": "test-visitor", "message": "yes", "conversation_id": conv_id},
    )

    [message] = (await client.get("/api/messages")).json()
    assert (message["code"], message["name"], message["status"]) == ("M-101", "Kayes", "new")
    assert (await client.get("/api/messages", params={"q": "m-101"})).json() == [message]

    handled = await client.patch(f"/api/messages/{message['id']}", json={"status": "handled"})
    assert handled.json()["status"] == "handled"
    unknown = await client.patch(f"/api/messages/{message['id']}", json={"status": "lost"})
    assert unknown.status_code == 400


async def test_the_owner_pages_through_messages(app: FastAPI, client: AsyncClient) -> None:
    store = await app.state.services.agents.store(DEV_WORKSPACE_ID)
    for body in ["first", "second", "third"]:
        store.now.advance(seconds=1)
        await store.add(TeamMessage, name="", phone="", body=body, best_time="")

    newest = (await client.get("/api/messages", params={"limit": 2})).json()
    assert [m["body"] for m in newest] == ["third", "second"]
    before = newest[-1]["created_at"]
    older = (await client.get("/api/messages", params={"limit": 2, "before": before})).json()
    assert [m["body"] for m in older] == ["first"]
    one = await client.get(f"/api/messages/{older[0]['id']}")
    assert one.json() == older[0]
    assert (await client.get("/api/messages", params={"limit": 101})).status_code == 422
