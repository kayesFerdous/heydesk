"""Who has a chat (I1): the owner gives any chat to anyone on the team; a moderator takes a free
one and gives back their own; a hand-off can go to the least busy moderator."""

from fastapi import FastAPI
from httpx import AsyncClient

from heydesk.ports.llm import ToolCall
from tests.conftest import DEV_KEY, new_business
from tests.integration.test_members import MOD, invite, join

OTHER = "nila@example.com"


async def chat(app: FastAPI, client: AsyncClient, visitor: str = "test-visitor") -> str:
    """A web chat with the dev agent; returns its conversation's id."""
    res = await client.post(f"/api/chat/{DEV_KEY}", json={"visitor_id": visitor, "message": "hi"})
    return res.text.split('"conversation_id": "')[1].split('"')[0]


async def assign(client: AsyncClient, conv: str, user_id: str | None) -> int:
    res = await client.put(f"/api/conversations/{conv}/assignee", json={"user_id": user_id})
    return res.status_code


async def test_a_moderator_takes_a_free_chat_but_not_a_colleagues(
    app: FastAPI, client: AsyncClient
) -> None:
    rafi_id, nila_id = await invite(client), await invite(client, OTHER)
    rafi, nila = await join(app), await join(app, OTHER)
    app.state.services.conversations.runtime.llm.script = ["Hello!"]
    conv = await chat(app, client)

    assert await assign(rafi, conv, rafi_id) == 200  # Take
    mine = (await rafi.get("/api/conversations?assigned=me")).json()
    assert [(c["id"], c["assigned_email"]) for c in mine] == [(conv, MOD)]
    assert (await client.get("/api/conversations?assigned=none")).json() == []

    assert await assign(nila, conv, nila_id) == 403  # a colleague's
    assert await assign(nila, conv, None) == 403
    assert await assign(rafi, conv, nila_id) == 403  # only the owner gives one to someone else
    assert await assign(rafi, conv, None) == 200  # Give back
    assert await assign(client, conv, nila_id) == 200  # the owner moves any
    assert (await nila.get(f"/api/conversations/{conv}")).json()["assigned_email"] == OTHER

    activity = (await client.get("/api/team/activity")).json()
    assert "PUT /api/conversations/{conversation_id}/assignee" in [e["action"] for e in activity]

    await client.delete(f"/api/team/members/{nila_id}")
    assert (await client.get(f"/api/conversations/{conv}")).json()["assigned_to"] is None


async def test_only_this_businesss_team_can_be_given_a_chat(
    app: FastAPI, client: AsyncClient
) -> None:
    app.state.services.conversations.runtime.llm.script = ["Hello!"]
    conv = await chat(app, client)
    other = await new_business(app)
    stranger = (await other.get("/api/team/members")).json()[0]["id"]
    assert await assign(client, conv, stranger) == 404
    assert await assign(other, conv, stranger) == 404  # not their conversation


async def test_a_hand_off_goes_to_the_least_busy_moderator(
    app: FastAPI, client: AsyncClient
) -> None:
    rafi_id, nila_id = await invite(client), await invite(client, OTHER)
    await join(app), await join(app, OTHER)
    await invite(client, "late@example.com")  # invited, not joined: never picked
    llm = app.state.services.conversations.runtime.llm
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
    llm = app.state.services.conversations.runtime.llm
    llm.script = [ToolCall("c1", "handoff_to_human", {}), "Someone will help you."]
    waiting = await chat(app, client, "visitor-one")
    llm.script = ["Hello!"]
    await chat(app, client, "visitor-two")  # the agent has it: no one waits

    counts = {"needs_human": 1, "mine": 0, "unassigned": 1}
    assert (await rafi.get("/api/conversations/counts")).json() == counts
    assert await assign(rafi, waiting, rafi_id) == 200
    counts = {"needs_human": 1, "mine": 1, "unassigned": 0}
    assert (await rafi.get("/api/conversations/counts")).json() == counts
    team = (await client.get("/api/team/members")).json()
    assert {m["id"]: m["open_chats"] for m in team}[rafi_id] == 1

    await client.put(f"/api/conversations/{waiting}/done", json={"done": True})
    assert (await rafi.get("/api/conversations/counts")).json()["mine"] == 0  # out of sight
