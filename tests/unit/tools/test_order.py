from typing import Any

from heydesk.ports.llm import ToolCall
from heydesk.runtime.tool import ToolResult, run_tool
from heydesk.tools import order
from tests.conftest import make_ctx

CTX = make_ctx()


async def update(*lines: tuple[str, int]) -> ToolResult:
    args: dict[str, Any] = {"lines": [{"code": c, "quantity": q} for c, q in lines]}
    return await run_tool(order.TOOL, ToolCall("1", "update_order", args), CTX)


async def test_python_adds_up_the_order() -> None:
    result = await update(("chk-biryani", 2), ("borhani", 1))

    assert result.ok
    assert result.data == {
        "order": ["2 × Chicken Biryani = ৳900", "1 × Borhani = ৳130"],
        "total": "৳1,030",
    }
    assert result.state == {
        "cart": {
            "lines": [
                {"code": "chk-biryani", "name": "Chicken Biryani", "quantity": 2, "price": 45000},
                {"code": "borhani", "name": "Borhani", "quantity": 1, "price": 13000},
            ],
            "total": 103000,
        }
    }


async def test_the_same_item_twice_is_added_up() -> None:
    result = await update(("borhani", 1), ("borhani", 2))
    assert result.data["order"] == ["3 × Borhani = ৳390"]


async def test_an_empty_order_clears_the_cart() -> None:
    result = await update()
    assert result.ok and result.state == {"cart": {"lines": [], "total": 0}}


async def test_unknown_or_unavailable_items_are_refused() -> None:
    unknown = await update(("pizza", 1))
    assert not unknown.ok and "Not on the list: pizza" in unknown.data["error"]
    sold_out = await update(("mango-lassi", 1))
    assert not sold_out.ok and "Not available right now: Mango Lassi" in sold_out.data["error"]
    assert unknown.state is None and sold_out.state is None  # the cart stays as it was
