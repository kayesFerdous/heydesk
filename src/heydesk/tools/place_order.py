"""`place_order`: saves the order, only after a read-back and a yes (rule 2)."""

from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from heydesk.domain.values import NewOrder
from heydesk.runtime.guards import confirm
from heydesk.runtime.speech import money
from heydesk.runtime.tasks import current_task, describe, display_detail, filled_details, next_step
from heydesk.runtime.tool import Tool, ToolContext, ToolResult, fail


class PlaceOrderInput(BaseModel):
    """No arguments: the order is read from the conversation state."""


async def place_order(ctx: ToolContext, args: PlaceOrderInput) -> ToolResult:
    task_id = current_task(ctx.tasks, ctx.state)
    task = ctx.tasks[task_id] if task_id else None
    if task is None or task.finish_with != "place_order":
        return fail("There's no order yet. Take it with update_order first.")
    have = filled_details(task, ctx.state)
    missing = next_step(task, have)  # the same answer the state note gives
    if missing:
        return fail(f"The {missing.label} is still needed. Ask for it first.")

    tz = ZoneInfo(ctx.agent.timezone)
    today = ctx.now.astimezone(tz).date()
    pickup = datetime.combine(today, time.fromisoformat(have["pickup_time"]), tzinfo=tz)
    cart = ctx.state["cart"]
    order = NewOrder(
        lines=cart["lines"],
        total=cart["total"],
        currency=ctx.agent.currency,
        pickup_time=pickup.astimezone(UTC),
        customer_name=have["name"],
        customer_phone=have["phone"],
        note=have.get("note", ""),
    )
    details = "; ".join(describe(task, have, ctx.agent.currency))
    confirmed = await confirm(ctx.decider, order, details, ctx.agent_said, ctx.visitor_said)
    if confirmed is None:
        return fail(
            "Not placed: the visitor hasn't said a clear yes to a read-back. Read every detail "
            "back, ask for a clear yes, and call place_order only after it."
        )

    number = await ctx.orders.place(confirmed)
    data = {
        "order_number": number,
        "total": money(order.total, order.currency),
        "pickup_time": display_detail("time_today", have["pickup_time"], order.currency),
    }
    return ToolResult(ok=True, data=data, state={"task": None, "cart": {}, "details": {}})


TOOL = Tool(
    name="place_order",
    description=(
        "Place the order. Call it only after reading every detail back and hearing a clear yes. "
        "Takes no arguments. Returns the order number; tell it to the visitor."
    ),
    input_model=PlaceOrderInput,
    run=place_order,
    label="Placing your order…",
    filler="Placing that now.",
    timeout_s=10.0,  # the commit check is a network call
)
