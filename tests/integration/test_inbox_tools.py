"""Finding a chat: search, Done until the buyer writes again, and Snooze until a time."""

from datetime import timedelta
from typing import Any

from fastapi import FastAPI
from httpx import AsyncClient

from tests.conftest import DEV_KEY, FIXED_NOW, new_business
from tests.integration.test_team import OWNER, link_owner, to


async def web(client: AsyncClient, text: str, conversation: str | None = None) -> str:
    body = {"visitor_id": "test-visitor", "message": text, "conversation_id": conversation}
    res = await client.post(f"/api/chat/{DEV_KEY}", json=body)
    return res.text.split('"conversation_id": "')[1].split('"')[0]


async def listed(client: AsyncClient, query: str = "") -> list[str]:
    return [c["id"] for c in (await client.get(f"/api/conversations{query}")).json()]


async def put(client: AsyncClient, conversation: str, what: str, body: dict[str, Any]) -> Any:
    return await client.put(f"/api/conversations/{conversation}/{what}", json=body)


async def test_a_done_chat_comes_back_when_the_buyer_writes_again(
    app: FastAPI, client: AsyncClient
) -> None:
    app.state.services.conversations.llm.script = ["Hello!", "Sure."]
    conversation = await web(client, "hi")
    assert (await put(client, conversation, "done", {"done": True})).json()["done_at"] is not None
    assert await listed(client) == []
    assert await listed(client, "?done=true") == [conversation]

    await web(client, "one more thing", conversation)
    assert await listed(client) == [conversation]
    assert await listed(client, "?done=true") == []


async def test_a_snoozed_chat_comes_back_at_its_time_with_an_alert(
    app: FastAPI, client: AsyncClient
) -> None:
    await link_owner(app, client)
    services = app.state.services
    services.conversations.llm.script = ["Hello!"]
    conversation = await web(client, "hi")
    later = (FIXED_NOW + timedelta(hours=2)).isoformat()
    assert (await put(client, conversation, "snooze", {"until": later})).status_code == 200
    assert await listed(client) == []

    await services.inbox.wake_snoozed()  # not yet
    assert not any("snoozed" in text for text in to(app, OWNER))
    services.conversations.now.advance(hours=2)
    await services.inbox.wake_snoozed()
    assert await listed(client) == [conversation]
    assert any("A chat you snoozed is back" in text for text in to(app, OWNER))

    past = (FIXED_NOW - timedelta(hours=1)).isoformat()
    assert (await put(client, conversation, "snooze", {"until": past})).status_code == 422


async def test_search_finds_words_phones_and_codes_in_one_business(
    app: FastAPI, client: AsyncClient
) -> None:
    app.state.services.conversations.llm.script = ["Noted.", "Hello!"]
    first = await web(client, "my number is 01712 345678, order O-101")
    await put(client, first, "done", {"done": True})  # a search looks in done chats too
    await web(client, "do you have biryani?")

    assert await listed(client, "?q=01712") == [first]
    assert await listed(client, "?q=o-101") == [first]
    assert await listed(client, "?q=100%25") == []  # a % is a letter, not "anything"
    app.state.services.conversations.llm.script = ["জি আছে।"]
    bangla = await web(client, "পাঞ্জাবি আছে?")
    assert await listed(client, "?q=পাঞ্জাবি") == [bangla]
    other = await new_business(app)
    assert (await other.get("/api/conversations?q=01712")).json() == []
