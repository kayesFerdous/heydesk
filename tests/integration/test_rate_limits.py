"""Rate limits (D52): bursts get a 429, from one visitor, one address or one login."""

from typing import Any

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tests.conftest import DEV_KEY
from tests.integration.test_telegram import connect, message, send, sent

CHAT = f"/api/chat/{DEV_KEY}"


def settings(app: FastAPI) -> Any:
    return app.state.services.rate_limits.settings


async def test_a_visitor_sending_too_fast_gets_a_429(client: AsyncClient) -> None:
    body = {"visitor_id": "browser-token-1", "message": "hi"}
    for _ in range(6):
        assert (await client.post(CHAT, json=body)).status_code == 200

    too_fast = await client.post(CHAT, json=body)
    assert too_fast.status_code == 429
    assert too_fast.json()["error"]["code"] == "rate_limited"
    other = {"visitor_id": "browser-token-2", "message": "hi"}
    assert (await client.post(CHAT, json=other)).status_code == 200


async def test_new_visitor_ids_from_one_address_are_limited_too(
    app: FastAPI, client: AsyncClient
) -> None:
    settings(app).RATE_CHAT_PER_IP = "3/minute"
    for n in range(3):
        body = {"visitor_id": f"script-token-{n}", "message": "hi"}
        assert (await client.post(CHAT, json=body)).status_code == 200

    body = {"visitor_id": "script-token-9", "message": "hi"}
    assert (await client.post(CHAT, json=body)).status_code == 429


async def test_guessing_a_password_is_limited(app: FastAPI) -> None:
    anon = AsyncClient(transport=ASGITransport(app), base_url="http://test")
    wrong = {"email": "owner@example.com", "password": "not the password"}
    for _ in range(10):
        assert (await anon.post("/api/auth/login", json=wrong)).status_code == 401

    assert (await anon.post("/api/auth/login", json=wrong)).status_code == 429


async def test_a_telegram_burst_is_left_unanswered(app: FastAPI, client: AsyncClient) -> None:
    settings(app).RATE_CHAT_PER_VISITOR = "2/minute"
    path, secret = await connect(app, client)
    for _ in range(3):
        await send(client, path, secret, message("hi"))

    assert len(sent(app)) == 2  # the third got no model call and no reply
