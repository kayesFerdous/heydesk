"""The team on Telegram (D43): the owner gets alerts, and answers visitors by replying."""

from typing import Any

from fastapi import FastAPI
from httpx import AsyncClient

from heydesk.ports.llm import ToolCall
from heydesk.services.team import GIVEN_BACK, LINKED, OLD_LINK, PICK_A_CHAT, TAKEN_OVER
from tests.conftest import DEV_KEY
from tests.integration.test_messages import YES
from tests.integration.test_restaurant import place_an_order
from tests.integration.test_telegram import CHAT, connect, message, send, sent

OWNER = 777


def from_owner(text: str, replying_to: str | None = None) -> dict[str, Any]:
    update = message(text)
    update["message"]["chat"]["id"] = OWNER
    if replying_to is not None:
        update["message"]["reply_to_message"] = {"message_id": 9, "text": replying_to}
    return update


def to(app: FastAPI, chat: int) -> list[str]:
    return [text for chat_id, text in sent(app) if chat_id == str(chat)]


async def link_owner(app: FastAPI, client: AsyncClient) -> tuple[str, str]:
    path, secret = await connect(app, client)
    link = (await client.get("/api/agent/telegram")).json()["owner_link"]
    code = link.split("?start=")[1]
    await send(client, path, secret, from_owner(f"/start {code}"))
    return path, secret


async def test_the_owner_links_their_chat_and_hears_about_each_order(
    app: FastAPI, client: AsyncClient
) -> None:
    path, secret = await connect(app, client)
    await send(client, path, secret, from_owner("/start owner_not-the-code"))
    assert to(app, OWNER) == [OLD_LINK]

    await link_owner(app, client)
    assert to(app, OWNER)[-1] == LINKED
    assert (await client.get("/api/agent/telegram")).json()["owner_linked"] is True

    conv_id = await place_an_order(app, client)  # in the web chat

    alert = to(app, OWNER)[-1]
    assert alert.startswith("🛒 New order #101\n2 × Chicken Biryani, 1 × Borhani, total ৳1,030")
    assert "pickup today at 7:30 pm; name Kayes; phone 01712345678" in alert
    assert alert.endswith(f"Ref: {conv_id}")


async def test_a_message_for_the_team_is_an_alert_too(app: FastAPI, client: AsyncClient) -> None:
    await link_owner(app, client)
    runtime = app.state.services.conversations.runtime
    take = ToolCall("c", "take_message", {"message": "Do you cater weddings?"})
    runtime.llm.script = [take, "Shall I pass that on?", take, "Done."]
    runtime.decider.script = [{"read_back": 0.1, "said_yes": 0.1}, YES]
    first = await client.post(f"/api/chat/{DEV_KEY}", json={"message": "a message please"})
    conv_id = first.text.split('"conversation_id": "')[1].split('"')[0]
    await client.post(f"/api/chat/{DEV_KEY}", json={"message": "yes", "conversation_id": conv_id})

    alert = to(app, OWNER)[-1]
    assert alert.startswith("✉️ New message M-101\nMessage: Do you cater weddings?")
    assert alert.endswith(f"Ref: {conv_id}")  # no phone, but the owner can answer in the chat


async def test_the_owner_takes_over_a_telegram_chat_and_gives_it_back(
    app: FastAPI, client: AsyncClient
) -> None:
    path, secret = await link_owner(app, client)
    llm = app.state.services.conversations.runtime.llm
    llm.script = [ToolCall("c1", "handoff_to_human", {}), "Someone from the team will help."]
    await send(client, path, secret, message("can I talk to a person?"))

    alert = to(app, OWNER)[-1]
    assert alert.startswith("🙋 Someone wants a person")
    assert "Customer: can I talk to a person?" in alert

    await send(client, path, secret, from_owner("Hi, this is Rahim. How can I help?", alert))
    assert to(app, CHAT)[-1] == "Hi, this is Rahim. How can I help?"
    assert to(app, OWNER)[-1].startswith(TAKEN_OVER)

    # the agent stays quiet: the visitor's message goes to the owner
    asked = len(llm.requests)
    await send(client, path, secret, message("my order is late"))
    assert len(llm.requests) == asked
    forwarded = to(app, OWNER)[-1]
    assert forwarded.startswith("💬 Customer: my order is late")

    await send(client, path, secret, from_owner("/done", forwarded))
    assert to(app, OWNER)[-1].startswith(GIVEN_BACK)
    await send(client, path, secret, message("thanks"))
    assert to(app, CHAT)[-1] == "(fake model) You said: thanks"
    # the model sees what the team said, as the agent's words, without the mark
    history = llm.requests[-1].messages
    assert {"role": "assistant", "content": "Hi, this is Rahim. How can I help?"} in history


async def test_a_web_visitor_gets_the_team_reply_on_the_page(
    app: FastAPI, client: AsyncClient
) -> None:
    path, secret = await link_owner(app, client)
    first = await client.post(f"/api/chat/{DEV_KEY}", json={"message": "hello"})
    conv_id = first.text.split('"conversation_id": "')[1].split('"')[0]
    team = f"/api/chat/{DEV_KEY}/{conv_id}/team"
    assert (await client.get(team)).json() == {"replies": []}

    await send(client, path, secret, from_owner("Hello from the team!", f"…\nRef: {conv_id}"))
    assert (await client.get(team)).json() == {"replies": ["Hello from the team!"]}

    body = {"message": "great", "conversation_id": conv_id}
    quiet = await client.post(f"/api/chat/{DEV_KEY}", json=body)
    assert "event: token" not in quiet.text and "event: done" in quiet.text
    assert to(app, OWNER)[-1].startswith("💬 Customer: great")


async def test_the_owner_must_reply_to_an_alert(app: FastAPI, client: AsyncClient) -> None:
    path, secret = await link_owner(app, client)
    await send(client, path, secret, from_owner("hello?"))
    assert to(app, OWNER)[-1] == PICK_A_CHAT
