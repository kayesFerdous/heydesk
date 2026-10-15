"""Messenger (D60): an owner connects their Facebook Page, and visitors talk to the agent there."""

import hashlib
import hmac
import json
from typing import Any

from fastapi import FastAPI
from httpx import AsyncClient

from heydesk.ports.llm import ToolCall
from tests.conftest import SEEDS, new_business
from tests.integration.test_shop import kurti_with_photo, photo
from tests.integration.test_team import from_owner, link_owner, to
from tests.integration.test_telegram import send as to_telegram

TOKEN = "EAAB-a-page-access-token"
PAGE = "1001"  # FakeMessenger's page
PSID = "9001"
CDN = "https://scontent.example/photo.jpg"  # where Facebook keeps a photo a visitor sent
NOT_TEXT = "I can only read text messages for now. Please type your question."


def message(text: str | None = "hi", **extra: Any) -> dict[str, Any]:
    msg: dict[str, Any] = {"mid": "m1", **({"text": text} if text is not None else {}), **extra}
    event = {"sender": {"id": PSID}, "recipient": {"id": PAGE}, "message": msg}
    return {"object": "page", "entry": [{"id": PAGE, "messaging": [event]}]}


async def post(client: AsyncClient, update: dict[str, Any], secret: str = "app-secret") -> int:
    body = json.dumps(update).encode()
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    res = await client.post(
        "/api/messenger", content=body, headers={"X-Hub-Signature-256": signature}
    )
    return res.status_code


def sent(app: FastAPI) -> list[tuple[str, str]]:
    fake: Any = app.state.services.channels.messenger
    return list(fake.sent)


async def connect(client: AsyncClient) -> None:
    assert (
        await client.put("/api/agent/messenger", json={"token": TOKEN, "page_id": PAGE})
    ).status_code == 200


async def test_facebook_checks_the_webhook_with_our_verify_token(client: AsyncClient) -> None:
    query = {"hub.mode": "subscribe", "hub.challenge": "42"}
    res = await client.get("/api/messenger", params={**query, "hub.verify_token": "verify-me"})
    assert res.status_code == 200 and res.text == "42"
    res = await client.get("/api/messenger", params={**query, "hub.verify_token": "nope"})
    assert res.status_code == 404


async def test_an_owner_connects_their_page_and_gets_its_link(
    app: FastAPI, client: AsyncClient
) -> None:
    await connect(client)
    assert PAGE in app.state.services.channels.messenger.subscribed

    res = await client.get("/api/agent/messenger")
    assert res.json() == {"page_id": PAGE, "link": f"https://m.me/{PAGE}"}
    assert TOKEN not in res.text  # the token never leaves the server

    res = await client.put(
        "/api/agent/messenger", json={"token": "bad" + "x" * 20, "page_id": PAGE}
    )
    assert res.status_code == 422


async def test_the_agent_answers_and_the_next_message_continues(
    app: FastAPI, client: AsyncClient
) -> None:
    await connect(client)
    assert await post(client, message("hi")) == 200
    assert await post(client, message("again")) == 200

    assert sent(app) == [
        (PSID, "(fake model) You said: hi"),
        (PSID, "(fake model) You said: again"),
    ]


async def test_a_wrong_signature_is_a_404_and_nothing_is_answered(
    app: FastAPI, client: AsyncClient
) -> None:
    await connect(client)
    assert await post(client, message("hi"), secret="not-ours") == 404
    assert sent(app) == []


def image(**payload: Any) -> dict[str, Any]:
    return {"attachments": [{"type": "image", "payload": {"url": CDN, **payload}}]}


async def test_echoes_and_photos_to_a_desk_that_takes_none(
    app: FastAPI, client: AsyncClient
) -> None:
    await connect(client)
    await post(client, message("from the page", is_echo=True))
    await post(client, message(None, **image()))  # the restaurant takes no photos

    assert sent(app) == [(PSID, NOT_TEXT)]


async def test_a_buyer_sends_a_photo_and_gets_the_product_as_a_card(
    app: FastAPI, client: AsyncClient
) -> None:
    services = app.state.services
    await services.agents.ensure_dev_agent("shop", SEEDS / "shop.yaml")
    url = await kurti_with_photo(client)
    await connect(client)
    fake = services.channels.messenger
    fake.files[CDN] = photo("red")
    llm = services.conversations.runtime.llm
    llm.script = [ToolCall("c1", "show_photos", {"products": ["kurti"]}), "Is it this one?"]

    await post(client, message(None, **image()))

    said = llm.requests[0].messages[-1]["content"]
    assert said == "[Photo. Closest products in the shop: Cotton Kurti [kurti]]"
    card = {
        "name": "Cotton Kurti",
        "price": "৳950",
        "url": services.channels.settings.public_url + url,
    }
    assert fake.cards == [(PSID, [card])]
    assert sent(app) == [(PSID, "Is it this one?")]

    await post(client, message(None, **image(sticker_id=369239263222822)))  # a thumbs-up
    assert sent(app)[-1] == (PSID, NOT_TEXT)


async def test_a_page_belongs_to_one_business(app: FastAPI, client: AsyncClient) -> None:
    await connect(client)
    other = await new_business(app)
    assert (await other.post("/api/agent", json={"kind": "restaurant"})).status_code == 201
    res = await other.put("/api/agent/messenger", json={"token": TOKEN, "page_id": PAGE})
    assert res.status_code == 422


async def test_disconnecting_stops_the_page(app: FastAPI, client: AsyncClient) -> None:
    await connect(client)
    assert (await client.delete("/api/agent/messenger")).status_code == 204

    assert app.state.services.channels.messenger.subscribed == set()
    assert await post(client, message("hi")) == 200  # Facebook is told OK, and nothing answers
    assert sent(app) == []


async def test_the_owner_answers_a_messenger_visitor_from_telegram(
    app: FastAPI, client: AsyncClient
) -> None:
    path, secret = await link_owner(app, client)
    await connect(client)
    llm = app.state.services.conversations.runtime.llm
    llm.script = [ToolCall("c1", "handoff_to_human", {}), "Someone from the team will help."]
    await post(client, message("can I talk to a person?"))

    alert = to(app, 777)[-1]
    assert "on Messenger" in alert
    await to_telegram(client, path, secret, from_owner("Hi, this is Rahim.", alert))
    assert sent(app)[-1] == (PSID, "Hi, this is Rahim.")
