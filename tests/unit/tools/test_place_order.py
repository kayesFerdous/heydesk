from datetime import UTC, datetime
from typing import Any

from heydesk.adapters.decider.fake import FakeDecider
from heydesk.ports.llm import ToolCall
from heydesk.runtime.tool import ToolResult, run_tool
from heydesk.tools import place_order
from tests.conftest import CART, ListOrders, make_ctx

STATE = {
    "task": "order",
    "cart": CART,
    "details": {"pickup_time": "19:30", "name": "Kayes", "phone": "01712345678"},
}
YES = {"read_back": 0.95, "said_yes": 0.95}


async def place(state: dict[str, Any], decider: FakeDecider, orders: ListOrders) -> ToolResult:
    ctx = make_ctx(state, decider=decider, orders=orders, agent_said="…?", visitor_said="yes")
    return await run_tool(place_order.TOOL, ToolCall("1", "place_order", {}), ctx)


async def test_a_yes_places_the_order_and_closes_the_task() -> None:
    decider, orders = FakeDecider([YES]), ListOrders()
    result = await place(STATE, decider, orders)

    assert result.ok
    assert result.data == {"order_number": 101, "total": "৳1,030", "pickup_time": "today, 7:30 pm"}
    assert result.state == {"task": None, "cart": {}, "details": {}}  # the state note goes quiet
    [order] = orders.placed
    assert order.total == 103000 and order.lines == CART["lines"]
    assert order.pickup_time == datetime(2026, 9, 29, 13, 30, tzinfo=UTC)  # 7:30 pm in Dhaka
    assert (order.customer_name, order.customer_phone, order.note) == ("Kayes", "01712345678", "")
    # Jev checked the read-back against the details the visitor heard
    assert "pickup time = today, 7:30 pm" in decider.asked[0]["details_to_confirm"]


async def test_no_yes_places_nothing() -> None:
    orders = ListOrders()
    result = await place(STATE, FakeDecider(), orders)  # the fake says no

    assert not result.ok and "clear yes" in result.data["error"]
    assert result.state is None and orders.placed == []


async def test_a_missing_detail_is_asked_for_before_any_check() -> None:
    decider, orders = FakeDecider([YES]), ListOrders()
    state = STATE | {"details": {"pickup_time": "19:30", "name": "Kayes"}}
    result = await place(state, decider, orders)

    assert not result.ok and result.data["error"] == "The phone is still needed. Ask for it first."
    assert decider.asked == [] and orders.placed == []
