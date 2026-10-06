"""Two roles (D23): an owner sees only their business; an admin runs the platform."""

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update

from heydesk.models import User
from heydesk.services.agents import DEV_WORKSPACE_ID
from tests.conftest import DEV_KEY
from tests.integration.test_restaurant import place_an_order

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
        f"/api/admin/businesses/{DEV_WORKSPACE_ID}", json={"status": "suspended"}
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
