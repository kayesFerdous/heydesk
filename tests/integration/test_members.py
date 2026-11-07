"""The business's team: an owner adds moderators by email; a moderator runs the inbox,
not the settings; a removed one is out at once; the owner sees what the team changed."""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from heydesk.models import User
from heydesk.services.billing import PLANS
from tests.conftest import emailed_code, new_business

MOD = "rafi@example.com"


def invitee() -> User:
    return User(id="u1", email=MOD, password_hash="hash")


async def join(app: FastAPI, email: str = MOD) -> AsyncClient:
    """The invited moderator sets a password with the emailed code, and is logged in."""
    anon = AsyncClient(transport=ASGITransport(app), base_url="http://test")
    body = {"email": email, "code": emailed_code(app, email), "new_password": "rafi's password"}
    joined = await anon.post("/api/auth/reset", json=body)
    assert joined.status_code == 200, joined.text
    anon.headers["Authorization"] = f"Bearer {joined.json()['token']}"
    return anon


async def invite(client: AsyncClient, email: str = MOD) -> str:
    added = await client.post("/api/team/members", json={"email": email})
    assert added.status_code == 201, added.text
    return str(added.json()["id"])


async def test_an_invite_has_its_own_page_and_a_business_name_cannot_inject(
    app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    pages: list[str] = []
    mailer = app.state.services.users.mailer
    send = mailer.send

    async def keep_page(to: str, subject: str, text: str, html: str = "") -> None:
        pages.append(html)
        await send(to, subject, text, html)

    monkeypatch.setattr(mailer, "send", keep_page)
    user = invitee()
    await app.state.services.users.send_code(user, "invite", "<b>NOTE</b> Shop", "boss@example.com")
    await app.state.services.users.send_code(user, "reset")

    invite_page, reset_page = pages
    assert "How to join" in invite_page and "How to join" not in reset_page
    assert "&lt;b&gt;NOTE&lt;/b&gt; Shop" in invite_page  # escaped, and not swapped again
    assert "boss@example.com" in invite_page and user.email in invite_page
    assert "/sign-in" in invite_page


async def test_an_invited_moderator_joins_with_the_emailed_code(
    app: FastAPI, client: AsyncClient
) -> None:
    await invite(client)
    [row] = [m for m in (await client.get("/api/team/members")).json() if m["email"] == MOD]
    assert (row["role"], row["status"]) == ("moderator", "invited")
    to, subject, text = app.state.services.users.mailer.sent[-1]
    assert to == MOD and "to join" in subject and "owner@example.com added you" in text

    # Before joining there's no password they know, and no verify code to skip it with
    anon = AsyncClient(transport=ASGITransport(app), base_url="http://test")
    sent = len(app.state.services.users.mailer.sent)
    await anon.post("/api/auth/verify/resend", json={"email": MOD})
    assert len(app.state.services.users.mailer.sent) == sent

    moderator = await join(app)
    me = (await moderator.get("/api/auth/me")).json()
    owner = (await client.get("/api/auth/me")).json()
    assert me["role"] == "moderator" and me["business"] == owner["business"]
    members = (await client.get("/api/team/members")).json()
    assert [(m["role"], m["status"]) for m in members] == [
        ("owner", "active"),
        ("moderator", "active"),
    ]
    again = await client.post(f"/api/team/members/{members[1]['id']}/invite")
    assert again.status_code == 409  # they've joined


async def test_a_moderator_runs_the_inbox_and_reads_but_doesnt_change_the_settings(
    app: FastAPI, client: AsyncClient
) -> None:
    await invite(client)
    moderator = await join(app)
    for path in (
        "/api/conversations",
        "/api/shop/orders",
        "/api/products",
        "/api/agent",
        "/api/agent/documents",
    ):
        assert (await moderator.get(path)).status_code == 200, path
    products = (await moderator.get("/api/products")).json()
    assert (await moderator.put("/api/products", json=products)).status_code == 200
    for method, path in (
        ("PATCH", "/api/agent"),
        ("POST", "/api/agent/documents"),
        ("PUT", "/api/agent/telegram"),
        ("GET", "/api/team/members"),
        ("POST", "/api/team/members"),
    ):
        refused = await moderator.request(method, path, json={"email": "x@example.com"})
        assert refused.status_code == 403, (method, path)

    # nothing on the agent is theirs to change, not even the saved replies
    replies = {"saved_replies": ["Thanks!"]}
    assert (await moderator.patch("/api/agent", json=replies)).status_code == 403


async def test_a_removed_moderator_is_out_at_once_and_their_changes_stay(
    app: FastAPI, client: AsyncClient
) -> None:
    user_id = await invite(client)
    moderator = await join(app)
    products = (await moderator.get("/api/products")).json()
    await moderator.put("/api/products", json=products)
    assert (
        await moderator.patch("/api/shop/orders/nope", json={"status": "ready"})
    ).status_code == 404

    assert (await client.delete(f"/api/team/members/{user_id}")).status_code == 204
    assert (await moderator.get("/api/conversations")).status_code == 401
    assert (await moderator.get("/api/auth/me")).status_code == 401
    assert [m["role"] for m in (await client.get("/api/team/members")).json()] == ["owner"]

    activity = (await client.get("/api/team/activity")).json()
    done = [(e["actor_email"], e["action"]) for e in activity]
    assert (MOD, "PUT /api/products") in done  # kept, with the email they had
    assert all("orders" not in action for _, action in done)  # a write that failed isn't there
    assert ("owner@example.com", "DELETE /api/team/members/{user_id}") in done


async def test_an_email_joins_one_business_and_a_team_has_a_limit(
    app: FastAPI, client: AsyncClient
) -> None:
    other = await new_business(app, "cafe@example.com")
    taken = await client.post("/api/team/members", json={"email": "cafe@example.com"})
    assert taken.status_code == 409  # already an owner elsewhere
    user_id = await invite(client)
    assert (await client.post("/api/team/members", json={"email": MOD})).status_code == 409

    # Another business can't see, resend or remove our moderator
    assert MOD not in str((await other.get("/api/team/members")).json())
    assert (await other.delete(f"/api/team/members/{user_id}")).status_code == 404
    assert (await other.post(f"/api/team/members/{user_id}/invite")).status_code == 404
    # The owner isn't a moderator: they can't be removed
    owner_id = (await client.get("/api/team/members")).json()[0]["id"]
    assert (await client.delete(f"/api/team/members/{owner_id}")).status_code == 404

    services = app.state.services
    workspace_id = services.users.user_from_token(client.headers["Authorization"][7:]).workspace_id
    for n in range(PLANS["pro"].moderators - 1):  # the dev workspace is on Pro
        await services.members.invite(workspace_id, f"m{n}@example.com", owner_id)
    full = await client.post("/api/team/members", json={"email": "one-more@example.com"})
    assert full.status_code == 409
