"""Orders, messages and conversations through the API (design §9)."""

from fastapi import FastAPI
from httpx import AsyncClient

from heydesk.ports.llm import ToolCall
from tests.conftest import DEV_KEY

LINES = [{"code": "chk-biryani", "quantity": 2}, {"code": "borhani", "quantity": 1}]
DETAILS = {"pickup_time": "7:30 pm", "name": "Kayes", "phone": "01712345678"}
READ_BACK = "2 Chicken Biryani and 1 Borhani, ৳1,030, at 7:30 pm for Kayes. Shall I place it?"


async def place_an_order(app: FastAPI, client: AsyncClient) -> str:
    """A whole order on the chat API, with a yes. Returns the conversation id."""
    app.state.services.conversations.runtime.llm.script = [
        ToolCall("c1", "update_order", {"lines": LINES}),
        ToolCall("c2", "set_details", {"task": "order", "details": DETAILS}),
        READ_BACK,
        ToolCall("c3", "place_order", {}),
        "Done! Your order number is 101.",
    ]
    app.state.services.conversations.runtime.decider.script = [
        {"read_back": 0.95, "said_yes": 0.95}
    ]
    first = await client.post(f"/api/chat/{DEV_KEY}", json={"message": "two biryanis…"})
    conv_id = first.text.split('"conversation_id": "')[1].split('"')[0]
    await client.post(f"/api/chat/{DEV_KEY}", json={"message": "yes", "conversation_id": conv_id})
    return conv_id


async def test_an_order_placed_on_the_page_shows_up_with_its_conversation(
    app: FastAPI, client: AsyncClient
) -> None:
    conv_id = await place_an_order(app, client)

    [order] = (await client.get("/api/orders")).json()
    assert (order["number"], order["total"], order["customer_name"]) == (101, 103000, "Kayes")
    assert order["conversation_id"] == conv_id and order["status"] == "new"

    conv = (await client.get(f"/api/conversations/{conv_id}")).json()
    assert [m["role"] for m in conv["messages"]] == [
        "assistant",
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert conv["messages"][2]["text"] == READ_BACK  # tool calls and results are left out
    assert [o["number"] for o in conv["orders"]] == [101]
    assert [c["id"] for c in (await client.get("/api/conversations")).json()] == [conv_id]


async def test_an_orders_status_changes(app: FastAPI, client: AsyncClient) -> None:
    await place_an_order(app, client)
    [order] = (await client.get("/api/orders")).json()

    ready = await client.patch(f"/api/orders/{order['id']}", json={"status": "ready"})
    bad = await client.patch(f"/api/orders/{order['id']}", json={"status": "eaten"})

    assert ready.json()["status"] == "ready"
    assert bad.status_code == 422
    assert (await client.patch("/api/orders/nope", json={"status": "ready"})).status_code == 404


async def test_nothing_taken_yet_is_an_empty_list(client: AsyncClient) -> None:
    assert (await client.get("/api/requests")).json() == []
    assert (await client.get("/api/conversations?status=needs_human")).json() == []
    assert (await client.get("/api/conversations/nope")).status_code == 404
