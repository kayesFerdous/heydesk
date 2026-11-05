"""Messages for the team: taken only after a yes; the owner sees them in
/api/messages."""

from datetime import UTC, datetime

from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select

from heydesk.models import Conversation, Lead, TeamMessage, Ticket
from heydesk.ports.llm import ToolCall
from heydesk.services.agents import DEV_WORKSPACE_ID
from heydesk.shop import tools_for
from heydesk.shop.shared import ComplaintInput, TakeLeadInput, flag_complaint, take_lead
from tests.conftest import DEV_KEY, World, make_ctx, said_yes

MESSAGE = {"message": "Do you cater weddings?", "name": "Kayes", "phone": "01712345678"}


async def messages(world: World) -> list[TeamMessage]:
    async with world.sessions() as s:
        return list(await s.scalars(select(TeamMessage)))


async def test_the_shop_takes_messages_hands_off_and_declines_timepass() -> None:
    names = [t.name for t in tools_for(has_documents=False)]
    assert names[-6:] == [
        "take_message",
        "take_lead",
        "handoff_to_human",
        "flag_complaint",
        "off_topic",
        "remember",
    ]
    assert "search_knowledge" in [t.name for t in tools_for(True)]


async def test_a_message_is_saved_only_after_a_yes(world: World) -> None:
    agent = await world.agent()
    take = ToolCall("c", "take_message", MESSAGE)

    conversation = await world.say(agent, None, [take, "Shall I pass that on?"])
    assert (
        "Message: Do you cater weddings?; from Kayes; phone 01712345678"
        in await world.tool_said(conversation)
    )
    assert await messages(world) == []

    await world.say(agent, conversation, [take, "Done."], "yes")
    [saved] = await messages(world)
    assert (saved.code, saved.body, saved.conversation_id) == (
        "M-101",
        MESSAGE["message"],
        conversation,
    )


async def test_a_name_and_phone_are_optional(world: World) -> None:
    agent = await world.agent()
    take = ToolCall("c", "take_message", {"message": "Loved the biryani!"})

    conversation = await world.say(agent, None, [take, "Shall I pass that on?"])
    said = await world.tool_said(conversation)
    assert '"read_back": "Message: Loved the biryani!"' in said
    assert "ask once whether they'd like to leave a name and phone" in said

    await world.say(agent, conversation, [take, "Done."], "yes, no number needed")
    [saved] = await messages(world)
    assert (saved.body, saved.name, saved.phone) == ("Loved the biryani!", "", "")
    assert "can't call back" in await world.tool_said(conversation)

    bad = ToolCall("c", "take_message", {"message": "Call me", "phone": "12"})
    conversation = await world.say(agent, None, [bad, "What's your number?"])
    assert "doesn't look like a phone number" in await world.tool_said(conversation)


async def test_the_owner_sees_messages_and_marks_them_handled(
    app: FastAPI, client: AsyncClient
) -> None:
    runtime = app.state.services.conversations.runtime
    take = ToolCall("c", "take_message", MESSAGE)
    runtime.llm.script = [take, "Shall I pass that on?", take, "Done."]
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


async def test_the_owner_marks_a_hand_off_handled(app: FastAPI, client: AsyncClient) -> None:
    runtime = app.state.services.conversations.runtime
    runtime.llm.script = [ToolCall("c", "handoff_to_human", {}), "Someone will call you."]
    first = await client.post(
        f"/api/chat/{DEV_KEY}", json={"visitor_id": "test-visitor", "message": "a person please"}
    )
    conv_id = first.text.split('"conversation_id": "')[1].split('"')[0]
    [waiting] = (await client.get("/api/conversations", params={"status": "needs_human"})).json()
    assert waiting["id"] == conv_id

    handled = await client.patch(f"/api/conversations/{conv_id}", json={"status": "active"})
    assert handled.json()["status"] == "active"
    assert (await client.get("/api/conversations", params={"status": "needs_human"})).json() == []
    again = await client.patch(f"/api/conversations/{conv_id}", json={"status": "active"})
    assert again.json()["error"]["code"] == "invalid_transition"
    other = await client.patch(f"/api/conversations/{conv_id}", json={"status": "needs_human"})
    assert other.status_code == 422


async def test_the_inbox_lists_the_chats_the_team_is_answering(
    app: FastAPI, client: AsyncClient
) -> None:
    app.state.services.conversations.runtime.llm.script = ["Hello!"]
    first = await client.post(
        f"/api/chat/{DEV_KEY}", json={"visitor_id": "test-visitor", "message": "hi"}
    )
    conv_id = first.text.split('"conversation_id": "')[1].split('"')[0]
    async with app.state.sessions() as s, s.begin():
        (await s.get(Conversation, conv_id)).status = "human"  # the owner took it over

    [taken] = (await client.get("/api/conversations", params={"status": "human"})).json()
    assert taken["id"] == conv_id


async def test_a_complaint_hands_off_and_opens_one_ticket(
    world: World, app: FastAPI, client: AsyncClient
) -> None:
    """flag_complaint hands the chat to the team and opens a ticket, with an alert; a second
    complaint in the same chat keeps the one ticket, which stays open until solved."""
    agent = await world.agent()
    complaint = ToolCall("c1", "flag_complaint", {"reason": "parcel came damaged"})
    conversation = await world.say(
        agent, None, [complaint, "I'm sorry. Someone will call you."], "!!!"
    )
    assert (await world.conversation(conversation)).status == "needs_human"
    result = await world.store(agent, conversation).open_ticket("still angry")
    assert result[1] is False  # the same open ticket
    async with world.sessions() as s:
        [ticket] = list(await s.scalars(select(Ticket)))
    assert (ticket.code, ticket.status, ticket.reason) == ("C-101", "open", "parcel came damaged")

    services = app.state.services
    services.conversations.runtime.llm.script = [
        ToolCall("c1", "flag_complaint", {"reason": "rude rider"}),
        "Sorry about that.",
    ]
    await client.post(f"/api/chat/{DEV_KEY}", json={"visitor_id": "angry-visitor", "message": "!!"})
    [listed] = (await client.get("/api/tickets", params={"status": "open"})).json()
    assert (listed["code"], listed["reason"]) == ("C-101", "rude rider")
    solved = await client.patch(f"/api/tickets/{listed['id']}", json={"status": "solved"})
    assert solved.json()["status"] == "solved"
    assert (await client.get("/api/tickets", params={"status": "open"})).json() == []


async def test_a_new_complaint_alerts_the_owner_once(world: World) -> None:
    agent = await world.agent()
    conversation = await world.say(agent, None, ["Hello!"], "hi")
    ctx = make_ctx(world.store(agent, conversation), agent=agent.config)
    first = await flag_complaint(ctx, ComplaintInput(reason="cold food"))
    again = await flag_complaint(ctx, ComplaintInput(reason="still cold"))
    assert first.handoff and first.alert == "😠 Complaint C-101: cold food"
    assert again.handoff and again.alert is None


async def test_a_call_back_is_a_lead_at_the_time_python_read(
    world: World, app: FastAPI, client: AsyncClient
) -> None:
    """ "call me Friday 5pm" on Tue 29 Sep → a lead for Fri 2 Oct, 17:00 in Dhaka, saved only
    at the yes; the team taps the outcome, and a lost one's why is counted."""
    agent = await world.agent()
    conversation = await world.say(agent, None, ["Hello!"], "hi")
    ctx = make_ctx(world.store(agent, conversation), agent=agent.config)
    args = TakeLeadInput(when="friday 5pm", phone="01712345678", note="the family set")
    asked = await take_lead(ctx, args)
    assert asked.data["read_back"] == (
        "We call you on Fri 2 Oct, 5:00 pm at 01712345678; about the family set"
    )
    placed = await said_yes(take_lead, ctx, args)
    assert placed.data["code"] == "L-101" and placed.alert
    async with world.sessions() as s:
        [lead] = list(await s.scalars(select(Lead)))
    assert lead.call_at.replace(tzinfo=UTC) == datetime(2026, 10, 2, 11, tzinfo=UTC)  # 17:00 Dhaka
    no_time = await take_lead(ctx, TakeLeadInput(when="friday", phone="01712345678"))
    assert not no_time.ok

    store = await app.state.services.agents.store(DEV_WORKSPACE_ID)
    await store.add(
        Lead, status="open", name="", phone="01712345678", call_at=lead.call_at, note=""
    )
    [listed] = (await client.get("/api/leads", params={"status": "open"})).json()
    lost = await client.patch(
        f"/api/leads/{listed['id']}", json={"status": "lost", "why": "Price too high"}
    )
    assert (lost.json()["status"], lost.json()["why"]) == ("lost", "price too high")
    assert (await client.get("/api/leads/why")).json() == [{"why": "price too high", "count": 1}]
