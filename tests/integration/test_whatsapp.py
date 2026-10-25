"""WhatsApp (D103): an owner connects their number, and visitors talk to the agent there."""

import hashlib
import hmac
import json
from typing import Any

from fastapi import FastAPI
from httpx import AsyncClient

from heydesk.ports.llm import ToolCall
from tests.conftest import DEV_KEY, SEEDS, new_business
from tests.integration.test_shop import kurti_with_photo, photo
from tests.integration.test_team import from_owner, link_owner, to
from tests.integration.test_telegram import send as to_telegram

TOKEN = "EAAB-a-system-user-token"
ACCOUNT, PHONE = "2001", "3001"
FROM = "8801711000000"  # the visitor's WhatsApp number
NOT_TEXT = "I can only read text messages for now. Please type your question."


def message(text: str | None = "hi", **instead: Any) -> dict[str, Any]:
    """A webhook post with one message: text, or `instead` its type's fields."""
    msg: dict[str, Any] = {"from": FROM, "id": "wamid.1", "type": "text", "text": {"body": text}}
    if instead:
        msg = {"from": FROM, "id": "wamid.1", **instead}
    value = {"messaging_product": "whatsapp", "metadata": {"phone_number_id": PHONE}}
    contacts = [{"wa_id": FROM, "profile": {"name": "Karim"}}]
    change = {"field": "messages", "value": {**value, "contacts": contacts, "messages": [msg]}}
    return {"object": "whatsapp_business_account", "entry": [{"id": ACCOUNT, "changes": [change]}]}


async def post(client: AsyncClient, update: dict[str, Any], secret: str = "app-secret") -> int:
    body = json.dumps(update).encode()
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    res = await client.post(
        "/api/whatsapp", content=body, headers={"X-Hub-Signature-256": signature}
    )
    return res.status_code


def fake(app: FastAPI) -> Any:
    return app.state.services.channels.whatsapp


async def connect(client: AsyncClient) -> None:
    body = {"token": TOKEN, "account_id": ACCOUNT, "phone_id": PHONE}
    assert (await client.put("/api/agent/whatsapp", json=body)).status_code == 200


async def test_meta_checks_the_webhook_with_our_verify_token(client: AsyncClient) -> None:
    query = {"hub.mode": "subscribe", "hub.challenge": "42"}
    res = await client.get("/api/whatsapp", params={**query, "hub.verify_token": "verify-me"})
    assert res.status_code == 200 and res.text == "42"
    res = await client.get("/api/whatsapp", params={**query, "hub.verify_token": "nope"})
    assert res.status_code == 404


async def test_an_owner_connects_their_number(app: FastAPI, client: AsyncClient) -> None:
    await connect(client)
    assert PHONE in fake(app).subscribed

    res = await client.get("/api/agent/whatsapp")
    assert res.json() == {"phone_id": PHONE}
    assert TOKEN not in res.text  # the token never leaves the server

    body = {"token": "bad" + "x" * 20, "account_id": ACCOUNT, "phone_id": PHONE}
    assert (await client.put("/api/agent/whatsapp", json=body)).status_code == 422


async def test_the_agent_answers_and_status_posts_are_skipped(
    app: FastAPI, client: AsyncClient
) -> None:
    await connect(client)
    assert await post(client, message("hi")) == 200
    status = message()
    del status["entry"][0]["changes"][0]["value"]["messages"]  # a delivery receipt
    assert await post(client, status) == 200
    assert await post(client, message("again")) == 200

    assert fake(app).sent == [
        (FROM, "(fake model) You said: hi"),
        (FROM, "(fake model) You said: again"),
    ]
    [listed] = (await client.get("/api/conversations")).json()
    assert listed["name"] == "Karim"


async def test_a_wrong_signature_is_a_404_and_nothing_is_answered(
    app: FastAPI, client: AsyncClient
) -> None:
    await connect(client)
    assert await post(client, message("hi"), secret="not-ours") == 404
    assert fake(app).sent == []


async def test_a_voice_note_gets_text_only(app: FastAPI, client: AsyncClient) -> None:
    await connect(client)
    await post(client, message(type="audio", audio={"id": "m9"}))
    assert fake(app).sent == [(FROM, NOT_TEXT)]


async def test_a_buyer_sends_a_photo_and_taps_its_order_button(
    app: FastAPI, client: AsyncClient
) -> None:
    services = app.state.services
    await services.agents.ensure_dev_agent("shop", SEEDS / "shop.yaml")
    url = await kurti_with_photo(client)
    await connect(client)
    fake(app).files["m1"] = photo("red")
    llm = services.conversations.runtime.llm
    llm.script = [ToolCall("c1", "show_photos", {"products": ["kurti"]}), "Is it this one?"]

    await post(client, message(type="image", image={"id": "m1", "caption": "eta ache?"}))

    said = llm.requests[0].messages[-1]["content"]
    assert said == "[Photo. Closest products in the shop: Cotton Kurti [kurti]]\neta ache?"
    card = {
        "code": "kurti",
        "name": "Cotton Kurti",
        "price": "৳950",
        "url": services.channels.settings.public_url + url,
        "video": "",
        "choice": "Order [kurti]",
        "page": "https://heydesk.test/p/dev-shop/kurti?from=whatsapp",  # its page (D121)
    }
    assert fake(app).cards == [(FROM, [card])]

    tapped = {
        "type": "button_reply",
        "button_reply": {"id": "Order [kurti]", "title": "Order this"},
    }
    await post(client, message(type="interactive", interactive=tapped))
    assert llm.requests[-1].messages[-1]["content"] == "Order [kurti]"


async def test_a_number_belongs_to_one_business(app: FastAPI, client: AsyncClient) -> None:
    await connect(client)
    other = await new_business(app)
    assert (await other.post("/api/agent", json={"kind": "restaurant"})).status_code == 201
    body = {"token": TOKEN, "account_id": ACCOUNT, "phone_id": PHONE}
    assert (await other.put("/api/agent/whatsapp", json=body)).status_code == 422


async def test_disconnecting_stops_the_number(app: FastAPI, client: AsyncClient) -> None:
    await connect(client)
    assert (await client.delete("/api/agent/whatsapp")).status_code == 204
    assert await post(client, message("hi")) == 200  # Meta is told OK, and nothing answers
    assert fake(app).sent == []


async def test_the_owner_answers_a_whatsapp_visitor_from_telegram(
    app: FastAPI, client: AsyncClient
) -> None:
    path, secret = await link_owner(app, client)
    await connect(client)
    llm = app.state.services.conversations.runtime.llm
    llm.script = [ToolCall("c1", "handoff_to_human", {}), "Someone from the team will help."]
    await post(client, message("can I talk to a person?"))

    alert = to(app, 777)[-1]
    assert "on WhatsApp" in alert
    await to_telegram(client, path, secret, from_owner("Hi, this is Rahim.", alert))
    print("SENT", fake(app).sent)
    assert fake(app).sent[-1] == (FROM, "Hi, this is Rahim.")


async def test_the_list_filters_by_channel(client: AsyncClient) -> None:
    await connect(client)
    assert await post(client, message("hi")) == 200
    visitor = {"visitor_id": "test-visitor", "message": "hello"}
    assert (await client.post(f"/api/chat/{DEV_KEY}", json=visitor)).status_code == 200

    async def channels(**params: str) -> list[str]:
        res = await client.get("/api/conversations", params=params)
        return [c["channel"] for c in res.json()]

    assert sorted(await channels()) == ["web", "whatsapp"]
    assert await channels(channel="whatsapp") == ["whatsapp"]
    assert await channels(channel="web") == ["web"]
    assert await channels(channel="messenger") == []
    res = await client.get("/api/conversations", params={"channel": "fax"})
    assert res.status_code == 422


async def test_a_reply_to_an_earlier_message_quotes_it(app: FastAPI, client: AsyncClient) -> None:
    """D117: WhatsApp sends only the quoted message's id; the buyer's own was kept."""
    await connect(client)
    await post(client, message("Beef Bhuna"))
    reply = message("is it spicy?")
    said = reply["entry"][0]["changes"][0]["value"]["messages"][0]
    said |= {"id": "wamid.2", "context": {"id": "wamid.1"}}
    await post(client, reply)

    assert fake(app).sent[-1] == (
        FROM,
        '(fake model) You said: (replying to: "Beef Bhuna") is it spicy?',
    )


async def test_more_cards_than_photos_are_one_list(app: FastAPI, client: AsyncClient) -> None:
    """The cards with no photo (or past the first 3) are one list to tap (D125)."""
    services = app.state.services
    await services.agents.ensure_dev_agent("shop", SEEDS / "shop.yaml")
    await kurti_with_photo(client)
    await connect(client)
    llm = services.conversations.runtime.llm
    codes = ["kurti", "panjabi-cotton"]
    llm.script = [ToolCall("c1", "show_photos", {"products": codes}), "Which one?"]

    await post(client, message("panjabi dekhan"))

    assert [c["code"] for _, cards in fake(app).cards for c in cards] == ["kurti"]
    row = {"id": "Order [panjabi-cotton]", "title": "Cotton Panjabi", "description": "৳1,850"}
    assert fake(app).lists == [(FROM, "More to order:", [row])]
