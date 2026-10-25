"""Messenger (D60): an owner connects their Facebook Page, and visitors talk to the agent there."""

import asyncio
import hashlib
import hmac
import json
from typing import Any

from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select

from heydesk.desks.shop.models import ShopOrder
from heydesk.ports.llm import ToolCall
from tests.conftest import SEEDS, new_business
from tests.integration.test_shop import DETAILS, KURTI, kurti_with_photo, photo
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
        "code": "kurti",
        "name": "Cotton Kurti",
        "price": "৳950",
        "url": services.channels.settings.public_url + url,
        "video": "",
        "choice": "Order [kurti]",  # its button's tap (D99)
        "page": "https://heydesk.test/p/dev-shop/kurti?from=messenger",  # its page (D121)
    }
    assert fake.cards == [(PSID, [card])]
    assert sent(app) == [(PSID, "Is it this one?")]

    await post(client, message(None, **image(sticker_id=369239263222822)))  # a thumbs-up
    assert sent(app)[-1] == (PSID, NOT_TEXT)


async def test_a_products_card_opens_its_public_page(app: FastAPI, client: AsyncClient) -> None:
    """The page shows what a buyer may see, as HTML, with the way back to the chat; anything
    else, another business's agent included, is the same 404 (D121)."""
    services = app.state.services
    await services.agents.ensure_dev_agent("shop", SEEDS / "shop.yaml")
    url = await kurti_with_photo(client)
    await connect(client)
    products = (await client.get("/api/products")).json()
    kurti = next(p for p in products if p["code"] == "kurti")
    kurti["description"] = "<script>alert(1)</script>"
    await client.put("/api/products", json=products)

    page = await client.get("/p/dev-shop/kurti?from=messenger")
    assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
    assert "default-src 'none'" in page.headers["content-security-policy"]
    body = page.text
    assert kurti["name"] in body and "৳950" in body and url in body
    assert "<script>" not in body and "&lt;script&gt;" in body  # the owner's words, escaped
    assert f'href="https://m.me/{PAGE}"' in body and "Order [kurti]" in body
    assert "/api/" not in body and "workspace" not in body
    assert "m.me" not in (await client.get("/p/dev-shop/kurti?from=web")).text

    await services.agents.ensure_dev_agent("restaurant", SEEDS / "restaurant.yaml")
    for wrong in ("/p/dev-shop/no-such", "/p/nobody/kurti", "/p/dev-restaurant/kurti"):
        missing = await client.get(wrong)
        assert missing.status_code == 404 and "Not found" in missing.text


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


def postback(payload: str) -> dict[str, Any]:
    """A tap on a card's Order button (D99)."""
    tapped = {"title": "Order this", "payload": payload}
    event = {"sender": {"id": PSID}, "recipient": {"id": PAGE}, "postback": tapped}
    return {"object": "page", "entry": [{"id": PAGE, "messaging": [event]}]}


async def test_a_cards_button_is_a_message_and_is_signed(app: FastAPI, client: AsyncClient) -> None:
    await services_shop(app)
    await connect(client)
    llm = app.state.services.conversations.runtime.llm

    assert await post(client, postback("Order [kurti]"), secret="not-ours") == 404
    await post(client, postback("Order [kurti]"))

    assert llm.requests[0].messages[-1]["content"] == "Order [kurti]"
    assert sent(app) == [(PSID, "(fake model) You said: Order [kurti]")]


async def test_a_yes_tapped_with_no_read_back_saves_nothing(
    app: FastAPI, client: AsyncClient
) -> None:
    """A tap is only words: with no read-back before it, nothing is saved (rule 2), and the
    reply asks for a yes with Yes and Change buttons (D99)."""
    await services_shop(app)
    await connect(client)
    services = app.state.services
    llm = services.conversations.runtime.llm
    llm.script = [
        ToolCall("c1", "update_cart", {"lines": [KURTI]}),
        ToolCall("c2", "place_order", DETAILS),
        "1 Cotton Kurti… Shall I place it?",
    ]

    await post(client, message("Yes", quick_reply={"payload": "Yes"}))  # a quick reply's tap

    async with app.state.sessions() as s:
        assert list(await s.scalars(select(ShopOrder))) == []
    assert services.channels.messenger.choices == [(PSID, ["Yes", "Change"])]


async def services_shop(app: FastAPI) -> None:
    await app.state.services.agents.ensure_dev_agent("shop", SEEDS / "shop.yaml")


async def test_three_quick_messages_get_one_reply_after_seen_and_typing(
    app: FastAPI, client: AsyncClient
) -> None:
    """D117: the agent waits for the visitor's next message, then answers all of them."""
    await connect(client)
    channels = app.state.services.channels
    channels.settings.CHAT_WAIT_SECONDS = 0.05
    channel = await channels.messenger_page(PAGE)
    bubbles = [
        {"sender": {"id": PSID}, "message": {"mid": f"m{i}", "text": text}}
        for i, text in enumerate(["hi", "dam koto", "biryani"])
    ]
    await asyncio.gather(*(channels.answer_messenger(channel, b) for b in bubbles))

    assert sent(app) == [(PSID, "(fake model) You said: hi\ndam koto\nbiryani")]
    actions = channels.messenger.actions
    assert actions[:3] == [(PSID, "seen")] * 3 and (PSID, "typing") in actions[3:]


async def test_a_reply_to_an_earlier_message_quotes_it(app: FastAPI, client: AsyncClient) -> None:
    await connect(client)
    app.state.services.channels.messenger.messages["m0"] = {"text": "Chicken Biryani · ৳350"}
    await post(client, message("how much for two?", reply_to={"mid": "m0"}))

    said = '(replying to: "Chicken Biryani · ৳350") how much for two?'
    assert sent(app) == [(PSID, f"(fake model) You said: {said}")]


SHOP_PAGE = {"id": PAGE, "name": "Rina's Kurti", "token": TOKEN}


async def test_connect_with_facebook_lists_pages_and_connects_the_chosen_one(
    app: FastAPI, client: AsyncClient
) -> None:
    """D118: the owner logs in with Facebook and picks a Page; nothing to copy."""
    fake = app.state.services.channels.messenger
    other = {"id": "1002", "name": "Another Page", "token": "EAAB-another-page-token"}
    fake.logins["a-facebook-user-token"] = [SHOP_PAGE, other]

    res = await client.get("/api/agent/messenger/login")
    assert res.json() == {"app_id": "4242", "config_id": "777"}

    login = {"user_token": "a-facebook-user-token"}
    res = await client.post("/api/agent/messenger/pages", json=login)
    assert res.json() == [
        {"id": PAGE, "name": "Rina's Kurti"},
        {"id": "1002", "name": "Another Page"},
    ]
    assert TOKEN not in res.text  # a Page's token never leaves the server

    res = await client.post("/api/agent/messenger/facebook", json=login | {"page_id": PAGE})
    assert res.status_code == 200 and res.json()["page_id"] == PAGE
    assert PAGE in fake.subscribed
    await post(client, message("hi"))
    assert sent(app) == [(PSID, "(fake model) You said: hi")]


async def test_a_page_the_login_doesnt_manage_or_a_bad_login_is_refused(
    app: FastAPI, client: AsyncClient
) -> None:
    app.state.services.channels.messenger.logins["a-facebook-user-token"] = [SHOP_PAGE]
    login = {"user_token": "a-facebook-user-token", "page_id": "1002"}
    res = await client.post("/api/agent/messenger/facebook", json=login)
    assert res.status_code == 422 and "page_id" in res.json()["error"]["fields"]

    res = await client.post("/api/agent/messenger/pages", json={"user_token": "x" * 30})
    assert res.status_code == 422 and "user_token" in res.json()["error"]["fields"]
