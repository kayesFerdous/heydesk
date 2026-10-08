"""The restaurant's tools: keep the order up to date (Python adds it up, rule 1), and place it."""

from datetime import datetime

from pydantic import BaseModel, Field

from heydesk.desks.restaurant.store import RestaurantStore
from heydesk.desks.restaurant.values import NewOrder, cart_text
from heydesk.runtime import parse
from heydesk.runtime.guards import ask_for_yes, confirm
from heydesk.runtime.speech import clock_time, money
from heydesk.runtime.tool import Checks, Tool, ToolContext, ToolResult, fail

type Ctx = ToolContext[RestaurantStore]


class OrderLine(BaseModel):
    code: str = Field(description="The dish's code, shown in [brackets]")
    quantity: int = Field(ge=1, le=50)


class UpdateOrderInput(BaseModel):
    lines: list[OrderLine] = Field(
        description="Every dish in the order, not just the change. An empty list clears it."
    )


async def update_order(ctx: Ctx, args: UpdateOrderInput) -> ToolResult:
    hours = ctx.agent.hours
    if hours.is_set() and hours.on(ctx.local_now.date()) is None:  # pickup is today only
        return fail(
            f"We're closed today. Our hours: {hours.render()}. Tell the visitor; take no order."
        )
    wanted: dict[str, int] = {}  # code → quantity; the same dish twice is added up
    for line in args.lines:
        wanted[line.code] = wanted.get(line.code, 0) + line.quantity

    menu = {item.code: item for item in await ctx.store.menu()}
    unknown = [code for code in wanted if code not in menu]
    if unknown:
        return fail(f"Not on the menu: {', '.join(unknown)}. Use the codes in [brackets].")
    sold_out = [menu[code].name for code in wanted if not menu[code].available]
    if sold_out:
        return fail(f"Not available right now: {', '.join(sold_out)}. Tell the visitor.")

    currency = ctx.agent.currency
    lines, said = [], []  # for the state, and for the model
    for code, qty in wanted.items():
        item = menu[code]
        lines.append({"code": code, "name": item.name, "quantity": qty, "price": item.price})
        said.append(f"{qty} × {item.name} = {money(item.price * qty, currency)}")
    total = sum(menu[code].price * qty for code, qty in wanted.items())

    data = {"order": said, "total": money(total, currency)}
    return ToolResult(ok=True, data=data, state={"cart": {"lines": lines, "total": total}})


UPDATE_ORDER = Tool(
    name="update_order",
    description=(
        "Set the visitor's whole order: every dish and its quantity. Call it after every change. "
        "Returns each line and the total; tell the visitor the total exactly as given."
    ),
    input_model=UpdateOrderInput,
    run=update_order,
    label="Updating your order…",
)


class PlaceOrderInput(BaseModel):
    pickup_time: str = Field(default="", description="When they'll pick it up today, like 7:30 pm")
    name: str = Field(default="", description="The name for the order")
    phone: str = Field(default="", description="The visitor's phone number")
    note: str = Field(default="", description="Optional: anything the kitchen should know")


async def place_order(ctx: Ctx, args: PlaceOrderInput) -> ToolResult:
    cart = ctx.state.get("cart") or {}
    if not cart.get("lines"):
        return fail("There's no order yet. Take it with update_order first.")
    now = ctx.local_now
    check = Checks()
    pickup = check.take(
        "pickup_time", args.pickup_time, lambda said: parse.time_today(said, now, ctx.agent.hours)
    )
    name = check.take("name", args.name, parse.name)
    phone = check.take("phone", args.phone, parse.phone)
    if pickup is None or name is None or phone is None:
        return check.result()
    note = " ".join(args.note.split())[:500]

    currency = ctx.agent.currency
    order = NewOrder(
        lines=cart["lines"],
        total=cart["total"],
        currency=currency,
        pickup_at=datetime.combine(now.date(), pickup, tzinfo=now.tzinfo),
        name=name,
        phone=phone,
        note=note,
    )
    read_back = (
        f"{cart_text(cart, currency)}; pickup today at {clock_time(pickup)}; "
        f"name {name}; phone {phone}" + (f"; note: {note}" if note else "")
    )
    confirmed = await confirm(ctx.decider, order, read_back, ctx.agent_said, ctx.visitor_said)
    if confirmed is None:
        return ask_for_yes(read_back, "place_order")
    number = await ctx.store.place_order(confirmed)
    data = {
        "order_number": number,
        "total": money(order.total, currency),
        "pickup_time": f"today, {clock_time(pickup)}",
    }
    alert = f"🛒 New order #{number}\n{read_back}"
    return ToolResult(ok=True, data=data, state={"cart": {}}, alert=alert)


PLACE_ORDER = Tool(
    name="place_order",
    description=(
        "Place the order: the pickup time today, the name and the phone. Call it as soon as you "
        "have the pickup time: it checks we're open then and says what's still needed. When it "
        "has everything it gives you what to read back; call it again with the same details "
        "after a clear yes. Returns the order number; tell it to the visitor."
    ),
    input_model=PlaceOrderInput,
    run=place_order,
    label="Checking your order…",
    filler="One moment.",
    timeout_s=10.0,  # the commit check is a network call
)

TOOLS = [UPDATE_ORDER, PLACE_ORDER]
