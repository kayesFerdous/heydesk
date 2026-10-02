"""Sign up, log in, and one workspace never sees another's (rule 6)."""

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

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
    [agent] = (await client.get("/api/agents")).json()
    second = await as_second_account(app)

    assert (await second.get("/api/agents")).json() == []
    assert (await second.get(f"/api/agents/{agent['id']}")).status_code == 404
    assert (await second.patch(f"/api/agents/{agent['id']}", json={"faq": "x"})).status_code == 404
    assert (await second.get(f"/api/agents/{agent['id']}/items")).status_code == 404
    await second.aclose()


async def test_login_gives_a_token_that_works(app: FastAPI, client: AsyncClient) -> None:
    second = await as_second_account(app)
    login = {"email": "second@example.com", "password": NEW["password"]}

    token = (await client.post("/api/auth/login", json=login)).json()["token"]

    me = await second.get("/api/agents", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    await second.aclose()


async def test_wrong_password_no_token_or_a_bad_token_is_401(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as anon:
        wrong = await anon.post(
            "/api/auth/login", json={"email": "owner@example.com", "password": "nope"}
        )
        no_token = await anon.get("/api/agents")
        bad = await anon.get("/api/agents", headers={"Authorization": "Bearer junk"})

    assert [r.status_code for r in (wrong, no_token, bad)] == [401, 401, 401]


async def test_an_email_signs_up_once(app: FastAPI) -> None:
    second = await as_second_account(app)
    again = await second.post("/api/auth/signup", json=NEW | {"email": "second@example.com"})
    assert again.status_code == 409
    await second.aclose()
