"""The agent stops answering a spammer (I2): the team blocks a visitor or turns the AI off for
them, and a flood on a messaging app blocks them by itself."""

from typing import Any

from fastapi import FastAPI
from httpx import AsyncClient

from tests.conftest import DEV_KEY
from tests.integration.test_team import OWNER, link_owner, to
from tests.integration.test_telegram import connect, message, send, sent

VISITOR = "test-visitor"


async def web(client: AsyncClient, text: str = "hi", conv: str | None = None) -> Any:
    body = {"visitor_id": VISITOR, "message": text, "conversation_id": conv}
    return await client.post(f"/api/chat/{DEV_KEY}", json=body)


def conv_id(res: Any) -> str:
    return str(res.text.split('"conversation_id": "')[1].split('"')[0])


async def change(client: AsyncClient, conv: str, **body: bool) -> dict[str, Any]:
    visitor = (await client.get(f"/api/conversations/{conv}")).json()["visitor"]
    res = await client.patch(f"/api/visitors/{visitor['id']}", json=body)
    assert res.status_code == 200, res.text
    return dict(res.json())


async def test_a_blocked_visitor_gets_no_turn_until_unblocked(
    app: FastAPI, client: AsyncClient
) -> None:
    llm = app.state.services.conversations.runtime.llm
    llm.script = ["Hello!", "Back again."]
    conv = conv_id(await web(client))
    blocked = await change(client, conv, blocked=True)
    assert (blocked["blocked"], blocked["blocked_why"], blocked["blocked_by"]) == (
        True,
        "team",
        "owner@example.com",
    )
    assert blocked["blocked_until"] is None  # until someone unblocks them

    asked = len(llm.requests)
    refused = await web(client, "spam spam", conv)
    assert refused.status_code == 429 and "can't take more messages" in refused.text
    assert len(llm.requests) == asked  # no model call
    said = (await client.get(f"/api/conversations/{conv}")).json()["messages"]
    assert "spam spam" not in [line["text"] for line in said]  # nothing kept from a block

    [row] = (await client.get("/api/visitors/blocked")).json()
    assert (row["conversation_id"], row["channel"], row["blocked_why"]) == (conv, "web", "team")

    await change(client, conv, blocked=False)
    assert (await web(client, "sorry", conv)).status_code == 200
    assert (await client.get("/api/visitors/blocked")).json() == []


async def test_a_blocked_buyer_on_telegram_is_told_once(app: FastAPI, client: AsyncClient) -> None:
    path, secret = await connect(app, client)
    app.state.services.conversations.runtime.llm.script = ["Hello!"]
    await send(client, path, secret, message("hi"))
    [conv] = (await client.get("/api/conversations")).json()
    await change(client, conv["id"], blocked=True)

    for _ in range(3):
        await send(client, path, secret, message("buy buy buy"))
    notices = [text for _, text in sent(app) if "can't take more messages" in text]
    assert len(notices) == 1  # then silence


async def test_ai_off_sends_their_messages_to_the_team(app: FastAPI, client: AsyncClient) -> None:
    path, secret = await link_owner(app, client)
    llm = app.state.services.conversations.runtime.llm
    llm.script = ["Hello!"]
    await send(client, path, secret, message("hi"))
    [conv] = (await client.get("/api/conversations")).json()
    assert (await change(client, conv["id"], ai_off=True))["ai_off"] is True

    asked, typed = len(llm.requests), len(app.state.services.channels.telegram.typing)
    await send(client, path, secret, message("where is my parcel?"))
    assert len(llm.requests) == asked  # no turn, nothing paid
    assert len(app.state.services.channels.telegram.typing) == typed  # and no "typing…"
    opened = (await client.get(f"/api/conversations/{conv['id']}")).json()
    assert opened["status"] == "human"
    assert "where is my parcel?" in [line["text"] for line in opened["messages"]]
    assert any("where is my parcel?" in text for text in to(app, OWNER))  # the owner hears it


async def test_ai_on_again_answers_them_again(app: FastAPI, client: AsyncClient) -> None:
    path, secret = await link_owner(app, client)
    llm = app.state.services.conversations.runtime.llm
    llm.script = ["Hello!", "It's on its way."]
    await send(client, path, secret, message("hi"))
    [conv] = (await client.get("/api/conversations")).json()
    await change(client, conv["id"], ai_off=True)
    await send(client, path, secret, message("anyone there?"))  # to the team

    assert (await change(client, conv["id"], ai_off=False))["ai_off"] is False
    asked = len(llm.requests)
    await send(client, path, secret, message("where is my parcel?"))
    assert len(llm.requests) == asked + 1  # the agent answers again
    opened = (await client.get(f"/api/conversations/{conv['id']}")).json()
    assert opened["status"] == "active"
    assert "It's on its way." in [line["text"] for line in opened["messages"]]


async def test_a_flood_on_a_messaging_app_blocks_for_a_day(
    app: FastAPI, client: AsyncClient
) -> None:
    path, secret = await connect(app, client)
    app.state.services.visitors.settings.FLOOD_MESSAGES = 3
    app.state.services.conversations.runtime.llm.script = ["Hi!"] * 3
    for n in range(5):
        await send(client, path, secret, message(f"spam {n}"))
    [row] = (await client.get("/api/visitors/blocked")).json()
    assert row["blocked_why"] == "flooding" and row["blocked_until"] is not None
    assert row["channel"] == "telegram"
