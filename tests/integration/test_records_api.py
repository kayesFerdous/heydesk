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

    [order] = (await client.get("/api/records")).json()
    assert (order["kind"], order["job"], order["number"]) == ("order", "order", 101)
    assert (order["total"], order["customer_name"]) == (103000, "Kayes")
    assert order["conversation_id"] == conv_id and order["status"] == "new"
    assert order["lines"][0]["quantity"] == 2

    conv = (await client.get(f"/api/conversations/{conv_id}")).json()
    assert [m["role"] for m in conv["messages"]] == [
        "assistant",
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert conv["messages"][2]["text"] == READ_BACK  # tool calls and results are left out
    assert [r["number"] for r in conv["records"]] == [101]
    assert [c["id"] for c in (await client.get("/api/conversations")).json()] == [conv_id]


async def test_the_inbox_lists_the_jobs_and_what_waits(app: FastAPI, client: AsyncClient) -> None:
    before = (await client.get("/api/inbox")).json()
    await place_an_order(app, client)
    after = (await client.get("/api/inbox")).json()

    assert [(e["job"], e["kind"]) for e in after] == [("order", "order"), ("message", "request")]
    assert (before[0]["waiting"], before[0]["total"]) == (0, 0)
    assert (after[0]["title"], after[0]["waiting"], after[0]["total"]) == ("Pickup order", 1, 1)


async def test_records_can_be_filtered_and_found(app: FastAPI, client: AsyncClient) -> None:
    await place_an_order(app, client)

    assert len((await client.get("/api/records?job=order")).json()) == 1
    assert len((await client.get("/api/records?job=message")).json()) == 0
    assert len((await client.get("/api/records?status=ready")).json()) == 0
    assert len((await client.get("/api/records?q=kay")).json()) == 1  # a name
    assert len((await client.get("/api/records?q=0171234")).json()) == 1  # a phone
    assert len((await client.get("/api/records?q=101")).json()) == 1  # an order number
    assert len((await client.get("/api/records?q=nobody")).json()) == 0


async def test_a_status_moves_only_where_its_kind_allows(app: FastAPI, client: AsyncClient) -> None:
    await place_an_order(app, client)
    [order] = (await client.get("/api/records")).json()
    url = f"/api/records/{order['id']}"

    skip = await client.patch(url, json={"status": "collected"})  # new → collected: no
    ready = await client.patch(url, json={"status": "ready"})
    back = await client.patch(url, json={"status": "new"})  # an order doesn't go back
    unknown = await client.patch(url, json={"status": "eaten"})
    handled = await client.patch(url, json={"status": "handled"})  # that's a message's status

    assert skip.status_code == 409 and skip.json()["error"]["code"] == "invalid_transition"
    assert ready.json()["status"] == "ready"
    assert back.status_code == 409
    assert unknown.status_code == 400 and "status is one of" in unknown.json()["error"]["message"]
    assert handled.status_code == 400
    assert (await client.get(url)).json()["status"] == "ready"
    nope = await client.patch("/api/records/nope", json={"status": "ready"})
    assert nope.status_code == 404


async def test_nothing_taken_yet_is_an_empty_list(client: AsyncClient) -> None:
    assert (await client.get("/api/records")).json() == []
    assert (await client.get("/api/conversations?status=needs_human")).json() == []
    assert (await client.get("/api/conversations/nope")).status_code == 404
