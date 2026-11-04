"""Sign up, log in, and one workspace never sees another's (rule 6)."""

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from heydesk.models import Workspace
from tests.conftest import emailed_code, new_business
from tests.integration.test_shop import place_an_order

NEW = {"email": "Second@Example.com", "password": "another long one", "workspace_name": "Cafe"}


async def as_second_account(app: FastAPI) -> AsyncClient:
    return await new_business(app, NEW["email"].lower())


async def test_a_second_account_cant_see_the_first_accounts_agent(
    app: FastAPI, client: AsyncClient
) -> None:
    second = await as_second_account(app)

    # Each account's /api/agent is its own, and the second has none
    assert (await second.get("/api/agent")).status_code == 404
    assert (await second.patch("/api/agent", json={"faq": "x"})).status_code == 404
    assert (await second.get("/api/products")).status_code == 404
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
    [order] = (await client.get("/api/shop/orders")).json()
    second = await as_second_account(app)

    await second.post("/api/agent", json={})  # a shop too
    for path in ("/api/shop/orders", "/api/messages", "/api/conversations"):
        assert (await second.get(path)).json() == [], path
    assert (await second.get(f"/api/conversations/{conv_id}")).status_code == 404
    assert (await second.get(f"/api/shop/orders/{order['id']}")).status_code == 404
    moved = await second.patch(f"/api/shop/orders/{order['id']}", json={"status": "cancelled"})
    assert moved.status_code == 404
    assert (await client.get("/api/shop/orders")).json()[0]["status"] == "new"
    await second.aclose()


async def test_a_new_owner_logs_in_only_after_typing_the_emailed_code(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as anon:
        assert (await anon.post("/api/auth/signup", json=NEW)).status_code == 204
        login = {"email": "second@example.com", "password": NEW["password"]}

        # The right password, but no code yet: no token
        early = await anon.post("/api/auth/login", json=login)
        assert early.status_code == 403
        assert early.json()["error"]["code"] == "email_not_verified"

        code = emailed_code(app, "second@example.com")
        wrong = "000000" if code != "000000" else "111111"
        bad = await anon.post("/api/auth/verify", json={"email": NEW["email"], "code": wrong})
        good = await anon.post("/api/auth/verify", json={"email": NEW["email"], "code": code})

        assert bad.status_code == 403
        assert good.status_code == 200 and good.json()["token"]
        assert (await anon.post("/api/auth/login", json=login)).status_code == 200


async def test_resend_says_nothing_about_who_has_an_account(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as anon:
        await anon.post("/api/auth/signup", json=NEW)
        known = await anon.post("/api/auth/verify/resend", json={"email": NEW["email"]})
        unknown = await anon.post("/api/auth/verify/resend", json={"email": "nobody@example.com"})

    assert known.status_code == unknown.status_code == 204
    sent_to = [to for to, *_ in app.state.services.users.mailer.sent]
    assert sent_to.count("second@example.com") == 2 and "nobody@example.com" not in sent_to


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
