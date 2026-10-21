"""Sign up, log in, and one workspace never sees another's (rule 6)."""

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from heydesk.models import Workspace
from tests.conftest import emailed_code
from tests.integration.test_restaurant import place_an_order

NEW = {"email": "Second@Example.com", "password": "another long one", "workspace_name": "Cafe"}


async def as_second_account(app: FastAPI) -> AsyncClient:
    anon = AsyncClient(transport=ASGITransport(app), base_url="http://test")
    signed_up = await anon.post("/api/auth/signup", json=NEW)
    assert signed_up.status_code == 201
    anon.headers["Authorization"] = f"Bearer {signed_up.json()['token']}"
    return anon


async def test_a_second_account_cant_see_the_first_accounts_agent(
    app: FastAPI, client: AsyncClient
) -> None:
    second = await as_second_account(app)

    # Each account's /api/agent is its own, and the second has none
    assert (await second.get("/api/agent")).status_code == 404
    assert (await second.patch("/api/agent", json={"faq": "x"})).status_code == 404
    assert (await second.get("/api/menu")).status_code == 404
    assert (await client.get("/api/agent")).json()["config"]["faq"] != "x"
    await second.aclose()


async def test_login_gives_a_token_that_works(app: FastAPI, client: AsyncClient) -> None:
    second = await as_second_account(app)
    login = {"email": "second@example.com", "password": NEW["password"]}

    token = (await client.post("/api/auth/login", json=login)).json()["token"]

    me = await second.get("/api/conversations", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    await second.aclose()


async def test_wrong_password_no_token_or_a_bad_token_is_401(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as anon:
        wrong = await anon.post(
            "/api/auth/login", json={"email": "owner@example.com", "password": "nope"}
        )
        no_token = await anon.get("/api/agent")
        bad = await anon.get("/api/agent", headers={"Authorization": "Bearer junk"})

    assert [r.status_code for r in (wrong, no_token, bad)] == [401, 401, 401]


async def test_an_email_signs_up_once_and_leaves_no_empty_business(app: FastAPI) -> None:
    second = await as_second_account(app)
    again = await second.post("/api/auth/signup", json=NEW | {"email": "second@example.com"})
    assert again.status_code == 409
    async with app.state.sessions() as s:
        cafes = await s.scalar(select(func.count()).where(Workspace.name == NEW["workspace_name"]))
    assert cafes == 1  # only the first signup's business
    await second.aclose()


async def test_a_second_account_sees_none_of_the_firsts_records(
    app: FastAPI, client: AsyncClient
) -> None:
    conv_id = await place_an_order(app, client)
    [order] = (await client.get("/api/orders")).json()
    second = await as_second_account(app)

    await second.post("/api/agent", json={"kind": "restaurant"})  # a restaurant too
    for path in ("/api/orders", "/api/messages", "/api/conversations"):
        assert (await second.get(path)).json() == [], path
    assert (await second.get(f"/api/conversations/{conv_id}")).status_code == 404
    assert (await second.get(f"/api/orders/{order['id']}")).status_code == 404
    moved = await second.patch(f"/api/orders/{order['id']}", json={"status": "cancelled"})
    assert moved.status_code == 404
    assert (await client.get("/api/orders")).json()[0]["status"] == "new"
    await second.aclose()


async def test_a_new_owners_agent_answers_once_they_type_the_emailed_code(app: FastAPI) -> None:
    second = await as_second_account(app)
    key = (await second.post("/api/agent", json={"kind": "restaurant"})).json()["public_key"]

    # Signed up: logged in, but the chat waits for the code (D93)
    assert (await second.get("/api/auth/me")).json()["email_verified"] is False
    chat = await second.get(f"/api/chat/{key}")
    assert chat.status_code == 404 and chat.json()["error"]["code"] == "unavailable"
    assert "Verify your email" in " ".join((await second.get("/api/agent")).json()["missing"])

    code = emailed_code(app, "second@example.com")
    wrong = "000000" if code != "000000" else "111111"
    assert (await second.post("/api/auth/verify", json={"code": wrong})).status_code == 403
    assert (await second.post("/api/auth/verify", json={"code": code})).status_code == 204

    assert (await second.get("/api/auth/me")).json()["email_verified"] is True
    assert (await second.get(f"/api/chat/{key}")).status_code == 200
    await second.aclose()


async def test_a_forgotten_password_is_reset_with_the_emailed_code_once(
    app: FastAPI, client: AsyncClient
) -> None:
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as anon:
        # An unknown email gets the same 204 and no email: it doesn't say who has an account
        nobody = await anon.post("/api/auth/forgot", json={"email": "nobody@example.com"})
        assert nobody.status_code == 204
        assert not [
            to for to, *_ in app.state.services.users.mailer.sent if to.startswith("nobody")
        ]

        await anon.post("/api/auth/forgot", json={"email": "Owner@Example.com"})
        code = emailed_code(app, "owner@example.com")
        reset = {"email": "owner@example.com", "code": code, "new_password": "a brand new one"}

        done = await anon.post("/api/auth/reset", json=reset)
        again = await anon.post("/api/auth/reset", json=reset | {"new_password": "a third one!"})
        old = {"email": "owner@example.com", "password": "a long password"}
        new = {"email": "owner@example.com", "password": "a brand new one"}

        assert done.status_code == 200 and done.json()["token"]
        assert again.status_code == 403  # the password changed, so the code is spent
        assert (await anon.post("/api/auth/login", json=old)).status_code == 401
        assert (await anon.post("/api/auth/login", json=new)).status_code == 200
