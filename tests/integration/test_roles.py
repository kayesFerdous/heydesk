"""Two roles (D23): an owner sees only their business; an admin runs the platform."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update

from heydesk.errors import Conflict
from heydesk.models import Conversation, Turn, User, Workspace
from heydesk.services.agents import DEV_WORKSPACE_ID
from tests.conftest import DEV_KEY, FIXED_NOW, new_business
from tests.integration.test_restaurant import place_an_order
from tests.integration.test_telegram import connect

ADMIN = {"email": "admin@heydesk.test", "password": "an admin password"}


async def as_admin(app: FastAPI, business: str | None = None) -> AsyncClient:
    await app.state.services.users.create_admin(ADMIN["email"], ADMIN["password"])
    anon = AsyncClient(transport=ASGITransport(app), base_url="http://test")
    token = (await anon.post("/api/auth/login", json=ADMIN)).json()["token"]
    anon.headers["Authorization"] = f"Bearer {token}"
    if business:
        anon.headers["X-Business"] = business
    return anon


async def test_an_owner_cant_even_find_the_admin_routes(client: AsyncClient) -> None:
    for path in ("/api/admin/businesses", "/api/admin/users", "/api/admin/audit"):
        assert (await client.get(path)).status_code == 404, path
    # X-Business means nothing to an owner: they always get their own business
    mine = await client.get("/api/agent", headers={"X-Business": "someone-else"})
    assert mine.status_code == 200


async def test_signup_makes_owners_and_me_says_who_you_are(
    app: FastAPI, client: AsyncClient
) -> None:
    me = (await client.get("/api/auth/me")).json()
    assert me["role"] == "owner" and me["business"]["name"] == "Dev"

    admin = await as_admin(app)
    assert (await admin.get("/api/auth/me")).json()["role"] == "admin"
    assert (await admin.get("/api/auth/me")).json()["business"] is None
    await admin.aclose()


async def test_an_admin_names_a_business_and_sees_it_like_its_owner(
    app: FastAPI, client: AsyncClient
) -> None:
    admin = await as_admin(app)

    assert (await admin.get("/api/agent")).status_code == 400  # which business?
    assert (await admin.get("/api/agent")).json()["error"]["code"] == "business_required"
    nowhere = await admin.get("/api/agent", headers={"X-Business": "nope"})
    assert nowhere.status_code == 404

    admin.headers["X-Business"] = DEV_WORKSPACE_ID
    agent = await admin.get("/api/agent")
    assert agent.json()["public_key"] == DEV_KEY
    await admin.aclose()


async def test_what_an_admin_changes_or_opens_is_in_the_audit_log(
    app: FastAPI, client: AsyncClient
) -> None:
    conv_id = await place_an_order(app, client)
    admin = await as_admin(app, DEV_WORKSPACE_ID)

    await admin.get("/api/orders")  # a list: not logged
    await admin.get(f"/api/conversations/{conv_id}")  # one chat opened
    await admin.patch("/api/agent", json={"faq": "Open on Eid."})  # a write

    log = (await admin.get("/api/admin/audit")).json()
    assert [(e["action"], e["actor_email"]) for e in log] == [
        ("PATCH /api/agent", ADMIN["email"]),
        ("GET /api/conversations/{conversation_id}", ADMIN["email"]),
    ]
    assert log[1]["target"] == f"conversation_id={conv_id}"
    assert log[0]["workspace_id"] == DEV_WORKSPACE_ID
    await admin.aclose()


async def test_a_suspended_business_is_unavailable_but_its_owner_can_log_in(
    app: FastAPI, client: AsyncClient
) -> None:
    admin = await as_admin(app)

    suspended = await admin.patch(
        f"/api/admin/businesses/{DEV_WORKSPACE_ID}",
        json={"status": "suspended", "reason": "Unpaid invoice"},
    )

    assert suspended.status_code == 204
    chat = await client.get(f"/api/chat/{DEV_KEY}")
    assert chat.status_code == 404 and chat.json()["error"]["code"] == "unavailable"
    assert (await client.get("/api/agent")).status_code == 200  # the owner still gets in
    assert (await client.get("/api/auth/me")).json()["business"]["status"] == "suspended"

    await admin.patch(f"/api/admin/businesses/{DEV_WORKSPACE_ID}", json={"status": "active"})
    assert (await client.get(f"/api/chat/{DEV_KEY}")).status_code == 200
    rows = (await admin.get("/api/admin/businesses")).json()
    assert [(r["name"], r["status"], r["kind"]) for r in rows] == [("Dev", "active", "restaurant")]
    await admin.aclose()


async def test_a_demoted_admin_is_out_at_once(app: FastAPI) -> None:
    admin = await as_admin(app)
    assert (await admin.get("/api/admin/users")).status_code == 200

    async with app.state.sessions() as s, s.begin():  # a business is needed for an owner row
        await s.execute(update(User).values(role="owner", workspace_id=DEV_WORKSPACE_ID))

    assert (await admin.get("/api/admin/users")).status_code == 404
    await admin.aclose()


async def test_an_admin_resets_a_forgotten_password(app: FastAPI, client: AsyncClient) -> None:
    admin = await as_admin(app)
    users = (await admin.get("/api/admin/users")).json()
    owner = next(u for u in users if u["role"] == "owner")

    temp = (await admin.post(f"/api/admin/users/{owner['id']}/password")).json()["password"]

    login = {"email": owner["email"], "password": temp}
    assert (await client.post("/api/auth/login", json=login)).status_code == 200
    old = {"email": owner["email"], "password": "a long password"}
    assert (await client.post("/api/auth/login", json=old)).status_code == 401
    log = (await admin.get("/api/admin/audit")).json()
    assert log[0]["action"] == "password reset"
    await admin.aclose()


async def test_from_the_command_line_an_admin_forgets_a_password_and_is_deleted(
    app: FastAPI, client: AsyncClient
) -> None:
    users = app.state.services.users
    await users.create_admin(ADMIN["email"], ADMIN["password"])
    assert [a.email for a in await users.admins()] == [ADMIN["email"]]

    await users.reset_password("Admin@HeyDesk.test", "a new password")

    new = {"email": ADMIN["email"], "password": "a new password"}
    assert (await client.post("/api/auth/login", json=new)).status_code == 200
    assert (await client.post("/api/auth/login", json=ADMIN)).status_code == 401
    await users.delete_admin(ADMIN["email"])
    assert await users.admins() == []


async def test_an_admin_in_the_audit_log_cant_be_deleted(app: FastAPI) -> None:
    admin = await as_admin(app, DEV_WORKSPACE_ID)
    await admin.patch("/api/agent", json={"faq": "Open on Eid."})  # now in the audit log

    with pytest.raises(Conflict):
        await app.state.services.users.delete_admin(ADMIN["email"])
    await admin.aclose()


async def test_an_owner_changes_the_temporary_password_an_admin_gave_them(
    app: FastAPI, client: AsyncClient
) -> None:
    admin = await as_admin(app)
    owner = next(u for u in (await admin.get("/api/admin/users")).json() if u["role"] == "owner")
    temp = (await admin.post(f"/api/admin/users/{owner['id']}/password")).json()["password"]

    wrong = {"current_password": "not it", "new_password": "my own password"}
    assert (await client.put("/api/auth/password", json=wrong)).status_code == 403
    right = {"current_password": temp, "new_password": "my own password"}
    assert (await client.put("/api/auth/password", json=right)).status_code == 204

    login = {"email": owner["email"], "password": "my own password"}
    assert (await client.post("/api/auth/login", json=login)).status_code == 200
    old = {"email": owner["email"], "password": temp}
    assert (await client.post("/api/auth/login", json=old)).status_code == 401  # the admin's
    await admin.aclose()


async def test_an_admin_cant_reset_another_admins_password(app: FastAPI) -> None:
    admin = await as_admin(app)
    await app.state.services.users.create_admin("other@heydesk.test", "another admin's")
    other = next(
        u
        for u in (await admin.get("/api/admin/users")).json()
        if u["email"] == "other@heydesk.test"
    )

    reset = await admin.post(f"/api/admin/users/{other['id']}/password")

    assert reset.status_code == 403
    login = {"email": "other@heydesk.test", "password": "another admin's"}
    assert (await admin.post("/api/auth/login", json=login)).status_code == 200
    await admin.aclose()


async def test_a_business_at_a_glance_for_the_admin(app: FastAPI, client: AsyncClient) -> None:
    await connect(app, client)  # its Telegram bot
    await place_an_order(app, client)
    async with app.state.sessions() as s, s.begin():  # the customer asked for a person
        await s.execute(update(Conversation).values(status="needs_human"))
    admin = await as_admin(app)

    page = (await admin.get(f"/api/admin/businesses/{DEV_WORKSPACE_ID}")).json()

    assert (page["name"], page["status"], page["kind"]) == ("Dev", "active", "restaurant")
    assert page["public_key"] == DEV_KEY
    assert [o["email"] for o in page["owners"]] == ["owner@example.com"]
    assert [(c["kind"], c["address"]) for c in page["channels"]] == [
        ("telegram", "spice_garden_bot")
    ]
    assert (page["payments"], page["documents"], page["sheet"]) == (None, 0, None)
    assert (page["conversations"], page["conversations_7_days"], page["waiting"]) == (1, 1, 1)
    assert page["last_activity_at"] is not None
    await admin.aclose()


async def test_only_an_admin_sees_a_business_page(app: FastAPI, client: AsyncClient) -> None:
    assert (await client.get(f"/api/admin/businesses/{DEV_WORKSPACE_ID}")).status_code == 404
    admin = await as_admin(app)
    assert (await admin.get("/api/admin/businesses/nope")).status_code == 404
    await admin.aclose()


async def test_a_suspension_says_why_to_admins_only(app: FastAPI, client: AsyncClient) -> None:
    admin = await as_admin(app)
    page = f"/api/admin/businesses/{DEV_WORKSPACE_ID}"

    no_reason = await admin.patch(page, json={"status": "suspended", "reason": "  "})
    assert no_reason.status_code == 422
    await admin.patch(page, json={"status": "suspended", "reason": "Spam reports"})

    assert (await admin.get(page)).json()["suspended_reason"] == "Spam reports"
    log = (await admin.get("/api/admin/audit")).json()
    assert (log[0]["action"], log[0]["target"]) == ("business suspended", "Spam reports")
    assert "Spam reports" not in (await client.get("/api/auth/me")).text  # not the owner's

    await admin.patch(page, json={"status": "active"})
    assert (await admin.get(page)).json()["suspended_reason"] == ""
    await admin.aclose()


def a_turn(ws: str, at: datetime, **kw: Any) -> Turn:
    row: dict[str, Any] = {
        "conversation_id": "c1",
        "channel": "web",
        "llm": "groq",
        "total_ms": 4000,
        "first_output_ms": 1000,
        "input_tokens": 100,
        "output_tokens": 10,
        "cost_micros": 0,
        "tools": [],
        "error": "",
        "handed_off": False,
        "interrupted": False,
    }
    return Turn(workspace_id=ws, at=at, **(row | kw))


async def test_the_overview_adds_up_turns_by_dhaka_day_and_business(app: FastAPI) -> None:
    await new_business(app)  # "Cafe"
    async with app.state.sessions() as s, s.begin():
        cafe = await s.scalar(select(Workspace.id).where(Workspace.name == "Cafe"))
        s.add_all(
            [  # the clock: Tue 29 Sep, 08:00 UTC = 14:00 in Dhaka
                a_turn(DEV_WORKSPACE_ID, FIXED_NOW - timedelta(hours=1), cost_micros=300),
                a_turn(DEV_WORKSPACE_ID, FIXED_NOW, first_output_ms=3000, error="503"),
                a_turn(cafe, datetime(2026, 9, 28, 19, tzinfo=UTC), handed_off=True),  # 01:00 Dhaka
                a_turn(DEV_WORKSPACE_ID, datetime(2026, 9, 28, 17, tzinfo=UTC), cost_micros=100),
                a_turn(DEV_WORKSPACE_ID, FIXED_NOW - timedelta(days=10)),  # before the 7 days
            ]
        )
    admin = await as_admin(app)

    seen = (await admin.get("/api/admin/overview")).json()

    today = seen["today"]
    assert (today["turns"], today["failed"], today["handed_off"]) == (3, 1, 1)
    assert (today["cost_micros"], today["input_tokens"]) == (300, 300)
    assert (today["first_reply_ms"], today["slow_first_reply_ms"]) == (1000, 3000)
    assert seen["period"]["turns"] == 4
    assert [(d["day"], d["turns"]) for d in seen["per_day"][-2:]] == [
        ("2026-09-28", 1),  # 23:00 in Dhaka is still the 28th
        ("2026-09-29", 3),
    ]
    assert len(seen["per_day"]) == 7
    assert [(b["name"], b["turns"], b["cost_micros"]) for b in seen["busiest"]] == [
        ("Dev", 3, 400),
        ("Cafe", 1, 0),
    ]
    await admin.aclose()


async def test_only_an_admin_sees_the_overview(client: AsyncClient) -> None:
    assert (await client.get("/api/admin/overview")).status_code == 404


async def test_a_business_page_has_its_own_last_7_days_and_failures(app: FastAPI) -> None:
    await new_business(app)  # "Cafe"
    async with app.state.sessions() as s, s.begin():
        cafe = await s.scalar(select(Workspace.id).where(Workspace.name == "Cafe"))
        s.add_all(
            [
                a_turn(DEV_WORKSPACE_ID, FIXED_NOW - timedelta(days=2), error="timeout"),
                a_turn(DEV_WORKSPACE_ID, FIXED_NOW - timedelta(hours=1), handed_off=True),
                a_turn(DEV_WORKSPACE_ID, FIXED_NOW, error="503", conversation_id="c2"),
                a_turn(DEV_WORKSPACE_ID, FIXED_NOW - timedelta(days=10), error="old"),  # too old
                a_turn(cafe, FIXED_NOW, error="cafe's"),  # another business
            ]
        )
    admin = await as_admin(app)

    usage = (await admin.get(f"/api/admin/businesses/{DEV_WORKSPACE_ID}")).json()["usage"]
    mine = (await admin.get(f"/api/admin/failed-turns?workspace_id={DEV_WORKSPACE_ID}")).json()
    every = (await admin.get("/api/admin/failed-turns")).json()

    assert (usage["turns"], usage["failed"], usage["handed_off"]) == (3, 2, 1)
    assert [(f["error"], f["conversation_id"]) for f in mine] == [
        ("503", "c2"),  # newest first
        ("timeout", "c1"),
        ("old", "c1"),  # the list isn't only this week
    ]
    assert mine[0]["business_name"] == "Dev" and mine[0]["business_status"] == "active"
    assert len(every) == 4 and "cafe's" in {f["error"] for f in every}
    assert (await admin.get("/api/admin/failed-turns?limit=1")).json()[0]["error"] in {
        "503",
        "cafe's",
    }
    await admin.aclose()


async def test_only_an_admin_sees_failed_turns(client: AsyncClient) -> None:
    assert (await client.get("/api/admin/failed-turns")).status_code == 404
