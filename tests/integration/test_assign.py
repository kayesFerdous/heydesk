"""Who has a chat: the owner gives any chat to anyone on the team; a moderator takes a free
one and gives back their own; a hand-off can go to the least busy moderator."""

from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select

from heydesk.models import Turn
from heydesk.ports.llm import ToolCall
from tests.conftest import DEV_KEY, new_business
from tests.integration.test_members import MOD, invite, join

OTHER = "nila@example.com"


async def chat(app: FastAPI, client: AsyncClient, visitor: str = "test-visitor") -> str:
    """A web chat with the dev agent; returns its conversation's id."""
    res = await client.post(f"/api/chat/{DEV_KEY}", json={"visitor_id": visitor, "message": "hi"})
    return res.text.split('"conversation_id": "')[1].split('"')[0]


async def assign(client: AsyncClient, conversation: str, user_id: str | None) -> int:
    res = await client.put(f"/api/conversations/{conversation}/assignee", json={"user_id": user_id})
    return res.status_code


async def test_a_moderator_takes_a_free_chat_but_not_a_colleagues(
    app: FastAPI, client: AsyncClient
) -> None:
    rafi_id, nila_id = await invite(client), await invite(client, OTHER)
    rafi, nila = await join(app), await join(app, OTHER)
    app.state.services.conversations.llm.script = ["Hello!"]
    conversation = await chat(app, client)

    assert await assign(rafi, conversation, rafi_id) == 200  # Take
    mine = (await rafi.get("/api/conversations?assigned=me")).json()
    assert [(c["id"], c["assigned_email"]) for c in mine] == [(conversation, MOD)]
    assert (await client.get("/api/conversations?assigned=none")).json() == []

    assert await assign(nila, conversation, nila_id) == 403  # a colleague's
    assert await assign(nila, conversation, None) == 403
    assert (
        await assign(rafi, conversation, nila_id) == 403
    )  # only the owner gives one to someone else
    assert await assign(rafi, conversation, None) == 200  # Give back
    assert await assign(client, conversation, nila_id) == 200  # the owner moves any
    assert (await nila.get(f"/api/conversations/{conversation}")).json()["assigned_email"] == OTHER

    activity = (await client.get("/api/team/activity")).json()
    assert "PUT /api/conversations/{conversation_id}/assignee" in [e["action"] for e in activity]

    await client.delete(f"/api/team/members/{nila_id}")
    assert (await client.get(f"/api/conversations/{conversation}")).json()["assigned_to"] is None


async def test_only_this_businesss_team_can_be_given_a_chat(
    app: FastAPI, client: AsyncClient
) -> None:
    app.state.services.conversations.llm.script = ["Hello!"]
    conversation = await chat(app, client)
    other = await new_business(app)
    stranger = (await other.get("/api/team/members")).json()[0]["id"]
    assert await assign(client, conversation, stranger) == 404
    assert await assign(other, conversation, stranger) == 404  # not their conversation


async def test_a_hand_off_goes_to_the_least_busy_moderator(
    app: FastAPI, client: AsyncClient
) -> None:
    rafi_id, nila_id = await invite(client), await invite(client, OTHER)
    await join(app), await join(app, OTHER)
    await invite(client, "late@example.com")  # invited, not joined: never picked
    llm = app.state.services.conversations.llm
    handoff = [ToolCall("c1", "handoff_to_human", {}), "Someone will help you."]

    llm.script = list(handoff)
    first = await chat(app, client, "visitor-one")
    assert (await client.get(f"/api/conversations/{first}")).json()["assigned_to"] is None  # off

    assert (await client.patch("/api/agent", json={"handoff": {"auto_assign": True}})).is_success
    await assign(client, first, rafi_id)  # Rafi has one open chat
    llm.script = list(handoff)
    second = await chat(app, client, "visitor-two")
    assert (await client.get(f"/api/conversations/{second}")).json()["assigned_to"] == nila_id


async def test_the_inbox_counts_who_waits_and_the_team_shows_open_chats(
    app: FastAPI, client: AsyncClient
) -> None:
    rafi_id = await invite(client)
    rafi = await join(app)
    llm = app.state.services.conversations.llm
    llm.script = [ToolCall("c1", "handoff_to_human", {}), "Someone will help you."]
    waiting = await chat(app, client, "visitor-one")
    llm.script = ["Hello!"]
    await chat(app, client, "visitor-two")  # the agent has it: no one waits

    counts = {"needs_human": 1, "mine": 0, "unassigned": 1, "orders_new": 0, "messages_new": 0}
    assert (await rafi.get("/api/conversations/counts")).json() == counts
    assert await assign(rafi, waiting, rafi_id) == 200
    counts = {"needs_human": 1, "mine": 1, "unassigned": 0, "orders_new": 0, "messages_new": 0}
    assert (await rafi.get("/api/conversations/counts")).json() == counts
    team = (await client.get("/api/team/members")).json()
    assert {m["id"]: m["open_chats"] for m in team}[rafi_id] == 1

    await client.put(f"/api/conversations/{waiting}/done", json={"done": True})
    assert (await rafi.get("/api/conversations/counts")).json()["mine"] == 0  # out of sight


async def test_labels_notes_and_a_reply_from_the_inbox(app: FastAPI, client: AsyncClient) -> None:
    """A label filters the list; a note stays the team's, out of the prompt and the chat; a
    reply from the inbox reaches the chat and takes it over."""
    llm = app.state.services.conversations.llm
    llm.script = ["Hello!"]
    conversation = await chat(app, client)
    other = await chat(app, client, visitor="another-visitor")
    res = await client.put(
        f"/api/conversations/{conversation}/labels", json={"labels": ["VIP", " vip", "COD"]}
    )
    assert res.json()["labels"] == ["vip", "cod"]
    listed = (await client.get("/api/conversations", params={"label": "vip"})).json()
    assert [c["id"] for c in listed] == [conversation] and other not in str(listed)

    note = await client.post(
        f"/api/conversations/{conversation}/notes", json={"text": "Asked for a refund before"}
    )
    assert note.status_code == 201 and note.json()["author"]
    assert [
        n["text"] for n in (await client.get(f"/api/conversations/{conversation}/notes")).json()
    ] == ["Asked for a refund before"]

    reply = await client.post(
        f"/api/conversations/{conversation}/reply", json={"text": "Ji, we'll call you"}
    )
    assert reply.json()["status"] == "human"
    team = (await client.get(f"/api/chat/{DEV_KEY}/{conversation}/team")).json()["replies"]
    assert team == ["Ji, we'll call you"]
    said = (await client.get(f"/api/conversations/{conversation}")).json()["messages"]
    assert said[-1]["role"] == "team"  # the inbox shows it as the team's, not the agent's

    await client.put(f"/api/conversations/{conversation}/takeover", json={"on": False})
    llm.script = ["Sure."]
    await client.post(
        f"/api/chat/{DEV_KEY}",
        json={"visitor_id": "test-visitor", "message": "thanks", "conversation_id": conversation},
    )
    prompt = str(llm.requests[-1].messages)
    assert "Ji, we'll call you" in prompt  # the team's words are in the history
    assert "refund before" not in prompt and "vip" not in prompt  # never a note or a label
    other_business = await new_business(app)
    assert (await other_business.get(f"/api/conversations/{conversation}/notes")).status_code == 404


async def test_a_draft_is_shown_to_the_team_and_not_sent(app: FastAPI, client: AsyncClient) -> None:
    """Summary, ask and draft: the model with no tools on the chat's history; the words come
    back, nothing is added to the chat or sent, and each is a traced turn."""
    services = app.state.services
    llm = services.conversations.llm
    llm.script = ["Hello!"]
    conversation = await chat(app, client)
    before = len((await client.get(f"/api/conversations/{conversation}")).json()["messages"])

    llm.script = ["Ji, your table is booked for 8 pm."]
    res = await client.post(f"/api/conversations/{conversation}/assist", json={"kind": "draft"})
    assert res.json() == {"text": "Ji, your table is booked for 8 pm."}
    asked = llm.requests[-1]
    assert asked.tools == [] and "Write the next message" in str(asked.messages[-1])

    llm.script = ["They asked about a table."]
    question = {"kind": "ask", "question": "What did {they} want?"}
    assert (
        await client.post(f"/api/conversations/{conversation}/assist", json=question)
    ).status_code == 200
    assert "What did {they} want?" in str(llm.requests[-1].messages[-1])  # never str.format

    after = (await client.get(f"/api/conversations/{conversation}")).json()["messages"]
    assert len(after) == before
    assert (await client.get(f"/api/chat/{DEV_KEY}/{conversation}/team")).json()["replies"] == []
    async with services.conversations.sessions() as s:
        channels = list(
            await s.scalars(select(Turn.channel).where(Turn.conversation_id == conversation))
        )
    assert channels.count("assist") == 2


async def test_a_hand_off_skips_a_moderator_who_isnt_available(
    app: FastAPI, client: AsyncClient
) -> None:
    """a moderator switches themself off; the owner can switch anyone."""
    rafi_id, nila_id = await invite(client), await invite(client, OTHER)
    rafi, nila = await join(app), await join(app, OTHER)
    await client.patch("/api/agent", json={"handoff": {"auto_assign": True}})
    llm = app.state.services.conversations.llm
    handoff = [ToolCall("c1", "handoff_to_human", {}), "Someone will help you."]

    assert (await rafi.put("/api/auth/me/available", json={"available": False})).status_code == 204
    assert (await rafi.get("/api/auth/me")).json()["available"] is False
    llm.script = list(handoff)
    first = await chat(app, client, "visitor-one")
    assert (await client.get(f"/api/conversations/{first}")).json()["assigned_to"] == nila_id

    off = {"available": False}
    assert (await nila.put(f"/api/team/members/{rafi_id}/available", json=off)).status_code == 403
    assert (await client.put(f"/api/team/members/{nila_id}/available", json=off)).status_code == 204
    team = {m["id"]: m["available"] for m in (await client.get("/api/team/members")).json()}
    assert (team[rafi_id], team[nila_id]) == (False, False)
    llm.script = list(handoff)
    second = await chat(app, client, "visitor-two")
    assert (await client.get(f"/api/conversations/{second}")).json()["assigned_to"] is None


async def test_a_solved_ticket_counts_for_who_marked_it(app: FastAPI, client: AsyncClient) -> None:
    """the team's numbers come from what each member did."""
    rafi_id = await invite(client)
    rafi = await join(app)
    llm = app.state.services.conversations.llm
    llm.script = [ToolCall("c1", "flag_complaint", {"reason": "cold food"}), "Sorry!"]
    await chat(app, client)
    [ticket] = (await rafi.get("/api/tickets")).json()
    await rafi.patch(f"/api/tickets/{ticket['id']}", json={"status": "solved"})

    stats = {m["id"]: m for m in (await client.get("/api/team/stats?days=1")).json()}
    assert stats[rafi_id]["tickets_solved"] == 1
    assert (await rafi.get("/api/team/stats")).status_code == 403  # the owner's
