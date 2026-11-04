"""Telegram: an owner connects their bot, and visitors talk to the agent there."""

from typing import Any

from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select, text

from heydesk.models import Channel, Conversation
from heydesk.ports.llm import ToolCall
from tests.conftest import SEEDS, new_business
from tests.integration.test_shop import kurti_with_photo, photo

TOKEN = "123456:a-token-from-botfather"
CHAT = 555


def message(text: str | None = "hi", chat_type: str = "private") -> dict[str, Any]:
    chat = {"id": CHAT, "type": chat_type, "first_name": "Rina", "last_name": "Akter"}
    msg: dict[str, Any] = {"message_id": 1, "chat": chat}
    if text is not None:
        msg["text"] = text
    return {"update_id": 1, "message": msg}


async def connect(app: FastAPI, client: AsyncClient) -> tuple[str, str]:
    """Connects the dev business's bot; returns the webhook's path and secret."""
    res = await client.put("/api/agent/telegram", json={"token": TOKEN})
    assert res.status_code == 200
    url, secret = app.state.services.channels.telegram.webhooks[TOKEN]
    return url.removeprefix("https://heydesk.test"), secret


async def send(client: AsyncClient, path: str, secret: str, update: dict[str, Any]) -> int:
    headers = {"X-Telegram-Bot-Api-Secret-Token": secret}
    res = await client.post(path, json=update, headers=headers)
    return res.status_code


def sent(app: FastAPI) -> list[tuple[str, str]]:
    fake: Any = app.state.services.channels.telegram
    return list(fake.sent)


async def telegram_conversations(app: FastAPI) -> list[Conversation]:
    async with app.state.sessions() as s:
        query = select(Conversation).where(Conversation.channel == "telegram")
        return list(await s.scalars(query.order_by(Conversation.created_at)))


async def test_an_owner_connects_their_bot_and_gets_its_link(
    app: FastAPI, client: AsyncClient
) -> None:
    path, secret = await connect(app, client)
    assert path.startswith("/api/telegram/") and secret

    res = await client.get("/api/agent/telegram")
    body = res.json()
    assert body["username"] == "spice_garden_bot"
    assert body["link"] == "https://t.me/spice_garden_bot"
    assert body["owner_link"].startswith("https://t.me/spice_garden_bot?start=owner_")
    assert body["owner_linked"] is False
    assert TOKEN not in res.text  # the token never leaves the server


async def test_the_token_is_encrypted_in_the_database(app: FastAPI, client: AsyncClient) -> None:
    path, secret = await connect(app, client)
    async with app.state.sessions() as s:
        stored = await s.scalar(text("SELECT token FROM channels"))
        channel = await s.scalar(select(Channel))
    assert stored and TOKEN not in stored  # a copy of the database can't run the bot
    assert channel is not None and channel.token == TOKEN  # the code reads it as it was given

    assert await send(client, path, secret, message("hi")) == 200
    assert sent(app)  # answered, with the token read back


async def test_a_wrong_token_is_a_problem_on_the_field(client: AsyncClient) -> None:
    res = await client.put("/api/agent/telegram", json={"token": "bad"})
    assert res.status_code == 422  # too short for a token

    res = await client.put("/api/agent/telegram", json={"token": "bad" + "x" * 20})
    assert res.status_code == 200  # the fake only refuses "bad" itself


async def test_the_agent_answers_and_the_next_message_continues(
    app: FastAPI, client: AsyncClient
) -> None:
    path, secret = await connect(app, client)

    assert await send(client, path, secret, message("hi")) == 200
    assert await send(client, path, secret, message("again")) == 200

    assert sent(app) == [
        (str(CHAT), "(fake model) You said: hi"),
        (str(CHAT), "(fake model) You said: again"),
    ]
    [conv] = await telegram_conversations(app)
    assert conv.contact == str(CHAT)
    assert [m["content"] for m in conv.messages if m["role"] == "user"] == ["hi", "again"]
    [listed] = (await client.get("/api/conversations")).json()
    assert listed["name"] == "Rina Akter"


async def test_start_sends_the_greeting_and_starts_afresh(
    app: FastAPI, client: AsyncClient
) -> None:
    path, secret = await connect(app, client)
    await send(client, path, secret, message("hi"))

    app.state.services.conversations.now.advance(minutes=1)
    await send(client, path, secret, message("/start"))
    await send(client, path, secret, message("hello"))

    assert sent(app)[1][1].startswith("Assalamu alaikum! Welcome to Nakshi Threads.")
    [conv] = await telegram_conversations(app)  # the same chat, started afresh
    remembered = conv.messages[conv.history_from :]
    assert remembered[0]["role"] == "assistant"  # the greeting they saw comes first
    assert [m["content"] for m in remembered if m["role"] == "user"] == ["hello"]


async def test_a_quiet_chat_starts_afresh_in_the_same_chat(
    app: FastAPI, client: AsyncClient
) -> None:
    """The team keeps one chat per contact, with its labels; the agent forgets."""
    path, secret = await connect(app, client)
    await send(client, path, secret, message("two biryanis"))
    [conv] = await telegram_conversations(app)
    await client.put(f"/api/conversations/{conv.id}/labels", json={"labels": ["vip"]})

    app.state.services.conversations.now.advance(hours=13)
    await send(client, path, secret, message("hi"))

    [conv] = await telegram_conversations(app)
    assert conv.history_from == 3 and conv.labels == ["vip"]
    prompt = app.state.services.conversations.runtime.llm.requests[-1]
    assert "two biryanis" not in str(prompt.messages)  # yesterday's isn't in its history

    whole = (await client.get(f"/api/conversations/{conv.id}")).json()
    assert [line["text"] for line in whole["messages"] if line["role"] == "user"] == [
        "two biryanis",
        "hi",
    ]
    assert whole["history_from"] == 3 and not whole["more"]
    newest = (await client.get(f"/api/conversations/{conv.id}", params={"limit": 1})).json()
    assert [line["text"] for line in newest["messages"]] == ["(fake model) You said: hi"]
    assert newest["more"] and newest["count"] == whole["count"] == 6
    before = newest["messages"][0]["index"]
    older = await client.get(f"/api/conversations/{conv.id}", params={"before": before})
    assert older.json()["messages"][-1]["text"] == "hi"


async def test_a_wrong_secret_is_a_404_and_nothing_is_answered(
    app: FastAPI, client: AsyncClient
) -> None:
    path, _ = await connect(app, client)
    assert await send(client, path, "not-the-secret", message("hi")) == 404
    assert await send(client, "/api/telegram/nope", "", message("hi")) == 404
    assert sent(app) == []


async def test_photos_and_group_chats(app: FastAPI, client: AsyncClient) -> None:
    path, secret = await connect(app, client)
    await send(client, path, secret, message(None))  # a photo: no text
    await send(client, path, secret, message("hi", chat_type="group"))

    assert sent(app) == [
        (str(CHAT), "I can only read text messages for now. Please type your question.")
    ]


async def test_another_business_has_no_bot(app: FastAPI, client: AsyncClient) -> None:
    await connect(app, client)
    other = await new_business(app)
    assert (await other.get("/api/agent/telegram")).status_code == 404
    assert (await other.delete("/api/agent/telegram")).status_code == 404


async def test_disconnecting_stops_the_bot(app: FastAPI, client: AsyncClient) -> None:
    path, secret = await connect(app, client)
    assert (await client.delete("/api/agent/telegram")).status_code == 204

    assert app.state.services.channels.telegram.webhooks == {}
    assert await send(client, path, secret, message("hi")) == 404


def with_photo(caption: str | None = None) -> dict[str, Any]:
    """A photo as Telegram sends it: the same picture in a few sizes, largest last."""
    update = message(None)
    sizes = [{"file_id": "small", "width": 90}, {"file_id": "large", "width": 1280}]
    update["message"]["photo"] = sizes
    if caption is not None:
        update["message"]["caption"] = caption
    return update


async def test_a_buyer_sends_a_photo_and_gets_the_product_photo_back(
    app: FastAPI, client: AsyncClient
) -> None:
    services = app.state.services
    await services.agents.ensure_dev_agent(SEEDS / "shop.yaml")
    await kurti_with_photo(client)
    path, secret = await connect(app, client)
    fake = services.channels.telegram
    fake.files["large"] = photo("red")
    llm = services.conversations.runtime.llm
    llm.script = [ToolCall("c1", "show_photos", {"products": ["kurti"]}), "Is it this one?"]

    await send(client, path, secret, with_photo("eta ache?"))

    said = llm.requests[0].messages[-1]["content"]
    assert said == "[Photo. Closest products in the shop: Cotton Kurti [kurti]]\neta ache?"
    assert fake.photos == [(str(CHAT), "Cotton Kurti · ৳950")]
    assert fake.choices == [(str(CHAT), ["Order [kurti]"])]  # its button
    [(to, page)] = fake.pages
    assert to == str(CHAT) and page.startswith(
        "https://heydesk.test/p/dev-shop/kurti?from=telegram&c="
    )
    assert sent(app) == [(str(CHAT), "Is it this one?")]


def tap(data: str) -> dict[str, Any]:
    """A tap on a button under the bot's photo, as Telegram sends it."""
    photo = {"message_id": 7, "chat": {"id": CHAT, "type": "private"}, "photo": [{"file_id": "x"}]}
    return {"update_id": 2, "callback_query": {"id": "cb1", "message": photo, "data": data}}


async def test_a_tap_is_a_message_and_the_reply_has_the_sizes_in_stock(
    app: FastAPI, client: AsyncClient
) -> None:
    services = app.state.services
    await services.agents.ensure_dev_agent(SEEDS / "shop.yaml")
    path, secret = await connect(app, client)
    fake = services.channels.telegram
    llm = services.conversations.runtime.llm
    line = {"code": "panjabi-cotton", "quantity": 1, "colour": "Navy"}
    llm.script = [ToolCall("c1", "update_cart", {"lines": [line]}), "Which size?"]

    await send(client, path, secret, tap("Order [panjabi-cotton]"))

    assert fake.answered == ["cb1"]  # its spinner stops
    assert llm.requests[0].messages[-1]["content"] == "Order [panjabi-cotton]"
    assert sent(app) == [(str(CHAT), "Which size?")]  # the bot's photo wasn't taken as theirs
    assert fake.choices == [(str(CHAT), ["M", "XL", "XXL"])]  # L in Navy is sold out


async def test_a_reply_to_an_earlier_message_quotes_it(app: FastAPI, client: AsyncClient) -> None:
    """Telegram sends the quoted message whole."""
    path, secret = await connect(app, client)
    update = message("is it spicy?")
    update["message"]["reply_to_message"] = {"message_id": 0, "text": "Beef Bhuna"}
    await send(client, path, secret, update)

    assert sent(app) == [
        (str(CHAT), '(fake model) You said: (replying to: "Beef Bhuna") is it spicy?')
    ]


async def test_a_reply_to_a_product_photo_names_it(app: FastAPI, client: AsyncClient) -> None:
    """a product photo's caption comes with the reply, so it names the product."""
    path, secret = await connect(app, client)
    update = message("I want this")
    update["message"]["reply_to_message"] = {"message_id": 5, "caption": "Cotton Kurti · ৳950"}
    await send(client, path, secret, update)

    assert sent(app) == [
        (str(CHAT), '(fake model) You said: (replying to: "Cotton Kurti · ৳950") I want this')
    ]


async def test_more_cards_than_photos_are_one_message_to_tap(
    app: FastAPI, client: AsyncClient
) -> None:
    """A photo each for the first 3 with one, the rest one message with an Order each."""
    services = app.state.services
    await services.agents.ensure_dev_agent(SEEDS / "shop.yaml")
    await kurti_with_photo(client)
    path, secret = await connect(app, client)
    fake = services.channels.telegram
    llm = services.conversations.runtime.llm
    codes = ["kurti", "panjabi-cotton", "kids-panjabi"]
    llm.script = [ToolCall("c1", "show_photos", {"products": codes}), "Which one?"]

    await send(client, path, secret, message("panjabi dekhan"))

    assert fake.photos == [(str(CHAT), "Cotton Kurti · ৳950")]
    [(_, text, keys)] = fake.cards
    assert text == "More to order:"
    assert keys == [
        {"title": "🛒 Cotton Panjabi · ৳1,850", "choice": "Order [panjabi-cotton]"},
        {"title": "🛒 Kids Panjabi · ৳890", "choice": "Order [kids-panjabi]"},
    ]


async def test_a_voice_note_is_answered_from_its_words(app: FastAPI, client: AsyncClient) -> None:
    """the note is downloaded, put in words, and the turn reads them as heard."""
    path, secret = await connect(app, client)
    app.state.services.channels.telegram.files["v1"] = b"OggS..."
    update = message(None)
    update["message"]["voice"] = {"file_id": "v1", "duration": 3}
    await send(client, path, secret, update)

    said = "(fake model) You said: [Voice note] Do you have the black kurti in M?"
    assert sent(app) == [(str(CHAT), said)]
    assert app.state.services.channels.transcriber.heard == ["voice.ogg"]


async def test_starting_again_points_the_bot_back_at_us(app: FastAPI, client: AsyncClient) -> None:
    """A bot connected before button taps, or on another tunnel, is set up again at start."""
    path, secret = await connect(app, client)
    services = app.state.services
    fake = services.channels.telegram
    fake.webhooks.clear()  # what Telegram had was old

    await services.channels.refresh_telegram()

    url, again = fake.webhooks[TOKEN]
    assert url.removeprefix("https://heydesk.test") == path and again == secret
