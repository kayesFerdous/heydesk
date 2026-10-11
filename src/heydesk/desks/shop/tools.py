"""The shop's tools: keep the cart up to date (Python adds it up, rule 1), place a
cash-on-delivery order, and track or cancel one with its code and the buyer's mobile.

A shop can take the delivery charge online first (D47): then `place_order` saves the order
`unpaid` and returns a payment link. No tool can mark it paid: only the payment check can."""

from typing import Any, cast

from pydantic import BaseModel, Field

from heydesk.desks.shared import awaiting_payment
from heydesk.desks.shop.models import ShopOrder
from heydesk.desks.shop.store import OutOfStock, ShopStore
from heydesk.desks.shop.values import (
    CANCELLABLE,
    STATUS_WORDS,
    NewOrder,
    OrderCancel,
    ShopConfig,
    cart_text,
    lines_text,
    match_zone,
    mobile,
)
from heydesk.domain.values import parse_code
from heydesk.runtime import parse
from heydesk.runtime.guards import ask_for_yes, confirm
from heydesk.runtime.speech import money
from heydesk.runtime.tool import Checks, Tool, ToolContext, ToolResult, fail

type Ctx = ToolContext[ShopStore]


class CartLine(BaseModel):
    code: str = Field(description="The product's code, shown in [brackets]")
    quantity: int = Field(ge=1, le=20)
    size: str = Field(default="", description="One of the product's sizes, if it has any")
    colour: str = Field(default="", description="One of the product's colours, if it has any")


class UpdateCartInput(BaseModel):
    lines: list[CartLine] = Field(
        description="Every product in the order, not just the change. An empty list clears it."
    )


async def update_cart(ctx: Ctx, args: UpdateCartInput) -> ToolResult:
    products = {p.code: p for p in await ctx.store.products()}
    unknown = [line.code for line in args.lines if line.code not in products]
    if unknown:
        return fail(f"Not in the shop: {', '.join(unknown)}. Use the codes in [brackets].")

    wanted: dict[tuple[str, str, str], int] = {}  # the same product, size and colour is added up
    problems = []
    for line in args.lines:
        product = products[line.code]
        try:
            key = (
                line.code,
                product.choose("size", line.size),
                product.choose("colour", line.colour),
            )
        except ValueError as error:
            problems.append(str(error))
            continue
        wanted[key] = wanted.get(key, 0) + line.quantity
    if problems:
        return fail(" ".join(problems))

    per_product: dict[str, int] = {}
    for (code, _, _), qty in wanted.items():
        per_product[code] = per_product.get(code, 0) + qty
    short = []
    for code, qty in per_product.items():
        stock = products[code].stock
        if stock is not None and qty > stock:
            short.append(
                f"{products[code].name}: " + (f"only {stock} left" if stock else "sold out")
            )
    if short:
        return fail(f"Not enough in stock. {'; '.join(short)}. Tell the visitor.")

    currency = ctx.agent.currency
    lines: list[dict[str, Any]] = [
        {
            "code": code,
            "name": products[code].name,
            "size": size,
            "colour": colour,
            "quantity": qty,
            "price": products[code].price,
        }
        for (code, size, colour), qty in wanted.items()
    ]
    subtotal = sum(line["price"] * line["quantity"] for line in lines)
    said = [
        f"{lines_text([line])} = {money(line['price'] * line['quantity'], currency)}"
        for line in lines
    ]
    data = {"order": said, "subtotal": money(subtotal, currency)}
    return ToolResult(ok=True, data=data, state={"cart": {"lines": lines, "subtotal": subtotal}})


UPDATE_CART = Tool(
    name="update_cart",
    description=(
        "Set the visitor's whole order: every product with its quantity, and its size and "
        "colour when it has them. Call it after every change. Checks the sizes, colours and "
        "stock, and returns each line and the subtotal; tell the visitor them exactly as given."
    ),
    input_model=UpdateCartInput,
    run=update_cart,
    label="Updating your order…",
)


class PlaceOrderInput(BaseModel):
    area: str = Field(
        default="", description="The delivery area, one of the shop's, like Inside Dhaka"
    )
    address: str = Field(
        default="", description="The full address: house, road, area, district, a landmark"
    )
    name: str = Field(default="", description="The name for the order")
    phone: str = Field(default="", description="The buyer's mobile number, for the rider")
    note: str = Field(default="", description="Optional: anything the shop should know")


async def place_order(ctx: Ctx, args: PlaceOrderInput) -> ToolResult:
    cart = ctx.state.get("cart") or {}
    if not cart.get("lines"):
        return fail("There's no order yet. Take it with update_cart first.")
    config = cast(ShopConfig, ctx.agent)
    check = Checks()
    zone = check.take("area", args.area, lambda said: match_zone(config.delivery, said))
    address = check.take("address", args.address, lambda said: parse.text(said, limit=300))
    name = check.take("name", args.name, parse.name)
    phone = check.take("phone", args.phone, mobile)

    currency = config.currency
    charge = config.delivery_charge(zone, cart["subtotal"]) if zone else 0
    data = {  # the visitor hears the charge and total as soon as the area is known
        "delivery": money(charge, currency) if charge else "free",
        "total": money(cart["subtotal"] + charge, currency),
    }
    if zone is None or address is None or name is None or phone is None:
        missing = check.result().data
        return ToolResult(ok=False, data=(data | missing) if zone else missing)

    order = NewOrder(
        lines=cart["lines"],
        subtotal=cart["subtotal"],
        delivery_charge=charge,
        currency=currency,
        zone=zone.name,
        address=address,
        name=name,
        phone=phone,
        note=" ".join(args.note.split())[:500],
        pay_first=charge > 0 and await ctx.store.pays_first(config),
    )
    how = (
        f"the delivery charge {data['delivery']} paid online first, then "
        f"{money(cart['subtotal'], currency)} in cash on delivery"
        if order.pay_first
        else "paid in cash on delivery"
    )
    read_back = (
        f"{cart_text(cart, currency)}; delivery {zone.name} {data['delivery']}; "
        f"total {data['total']}, {how}; to {address}; "
        f"name {name}; mobile {phone}" + (f"; note: {order.note}" if order.note else "")
    )
    confirmed = await confirm(ctx.decider, order, read_back, ctx.agent_said, ctx.visitor_said)
    if confirmed is None:
        return ask_for_yes(read_back, "place_order")
    try:
        saved = await ctx.store.place_order(confirmed)
    except OutOfStock as error:
        return fail(f"Sold out since the order was made: {error}. Tell the visitor.")
    code = saved.code
    if order.pay_first:  # the owner hears about it once it's paid (D47)
        link = ctx.store.pay_link(saved.id)
        return awaiting_payment(code, link, data["delivery"], saved.PAY_FOR, {"cart": {}})
    alert = f"🛍️ New order {code}\n{read_back}"
    return ToolResult(
        ok=True,
        data={
            "code": code,
            "total": data["total"],
            "next": f"Tell them the code {code}, and that the shop will call {phone} to "
            "confirm before sending it.",
        },
        state={"cart": {}},
        alert=alert,
    )


PLACE_ORDER = Tool(
    name="place_order",
    description=(
        "Place a cash-on-delivery order: the delivery area, the full address, the name and the "
        "mobile number. Call it as soon as you have the area: it gives the delivery charge and "
        "the total, and says what's still needed. When it has everything it gives you what to "
        "read back; call it again with the same details after a clear yes. Returns the order "
        "code; tell it to the visitor. If the shop takes the delivery charge first, it returns "
        "a payment link instead, and the order waits for the payment."
    ),
    input_model=PlaceOrderInput,
    run=place_order,
    label="Checking your order…",
    filler="One moment.",
    timeout_s=10.0,  # the commit check is a network call
)


class FindOrderInput(BaseModel):
    code: str = Field(description="The order code the visitor gave, like O-104")
    phone: str = Field(description="The mobile number the order was placed with")


async def _find(ctx: Ctx, code: str, phone: str) -> ShopOrder | ToolResult:
    """The order, if the code and the mobile both match. One answer for a wrong code and a
    wrong number, so a stranger can't find out whose order a code is."""
    parsed = parse_code(code)
    if parsed is None or parsed[0] not in ("", "O"):
        return fail("That isn't an order code: they look like O-104. Ask again.")
    try:
        number = mobile(phone)
    except ValueError as error:
        return fail(str(error))
    found = await ctx.store.order(parsed[1])
    if found is None or found.phone != number:
        return fail("No order matches that code and mobile number. Ask them to check both.")
    return found


async def track_order(ctx: Ctx, args: FindOrderInput) -> ToolResult:
    found = await _find(ctx, args.code, args.phone)
    if isinstance(found, ToolResult):
        return found
    data = {
        "order": found.code,
        "status": STATUS_WORDS[found.status],
        "items": lines_text(found.lines),
        "total": money(found.total, found.currency),
        "cash_on_delivery": money(found.total - found.paid, found.currency),
        "delivery_area": found.zone,
    }
    if found.paid:
        data["paid_online"] = money(found.paid, found.currency)
    if found.status == "unpaid":
        data["pay_link"] = ctx.store.pay_link(found.id)
    zones = cast(ShopConfig, ctx.agent).delivery
    zone = next((z for z in zones if z.name == found.zone), None)
    if found.status in ("confirmed", "shipped") and zone and zone.days:
        data["usually_takes"] = zone.days
    if found.tracking:
        data["courier"] = found.tracking
    return ToolResult(ok=True, data=data)


TRACK_ORDER = Tool(
    name="track_order",
    description=(
        "Where an order is. First ask for its code (like O-104), then the mobile number it was "
        "placed with. Returns its status, items, total and the courier if it has one."
    ),
    input_model=FindOrderInput,
    run=track_order,
    label="Finding your order…",
    filler="Let me look.",
)


async def cancel_order(ctx: Ctx, args: FindOrderInput) -> ToolResult:
    found = await _find(ctx, args.code, args.phone)
    if isinstance(found, ToolResult):
        return found
    if found.status not in CANCELLABLE:
        return fail(
            f"Order {found.code} is {STATUS_WORDS[found.status]}: it can't be cancelled now. "
            "Offer to take a message for the team about a return."
        )
    read_back = (
        f"Cancel order {found.code}: {lines_text(found.lines)}, "
        f"total {money(found.total, found.currency)}"
    )
    confirmed = await confirm(
        ctx.decider, OrderCancel(found.number), read_back, ctx.agent_said, ctx.visitor_said
    )
    if confirmed is None:
        return ask_for_yes(read_back, "cancel_order")
    await ctx.store.cancel_order(confirmed)
    return ToolResult(ok=True, data={"done": read_back}, alert=f"❌ Order {found.code} cancelled")


CANCEL_ORDER = Tool(
    name="cancel_order",
    description=(
        "Cancel an order before it's handed to the courier. First ask for its code (like O-104), "
        "then the mobile number it was placed with. The first call finds it and gives what to "
        "read back; call again after a clear yes."
    ),
    input_model=FindOrderInput,
    run=cancel_order,
    label="Finding your order…",
    timeout_s=10.0,
)

TOOLS = [UPDATE_CART, PLACE_ORDER, TRACK_ORDER, CANCEL_ORDER]
