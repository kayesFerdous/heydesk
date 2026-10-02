"""One error shape everywhere: {"error": {"code", "message"}} (design §9)."""

from fastapi import FastAPI
from httpx import AsyncClient

from tests.conftest import DEV_KEY


async def test_fastapis_own_errors_have_the_same_shape(client: AsyncClient) -> None:
    unknown = await client.get("/api/nothing-here")
    invalid = await client.post("/api/auth/signup", json={"email": "x"})

    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "not_found"
    assert invalid.status_code == 422
    error = invalid.json()["error"]
    assert error["code"] == "invalid_request"
    assert {"body.email", "body.password", "body.workspace_name"} <= set(error["fields"])


async def test_a_full_conversation_asks_for_a_new_one(app: FastAPI, client: AsyncClient) -> None:
    app.state.settings.MAX_TURNS = 2
    app.state.services.conversations.runtime.llm.script = ["One.", "Two."]
    url = f"/api/chat/{DEV_KEY}"
    first = await client.post(url, json={"message": "hi"})
    conv_id = first.text.split('"conversation_id": "')[1].split('"')[0]
    await client.post(url, json={"message": "and?", "conversation_id": conv_id})

    third = await client.post(url, json={"message": "more", "conversation_id": conv_id})

    assert third.status_code == 409
    assert third.json()["error"]["code"] == "conversation_full"
