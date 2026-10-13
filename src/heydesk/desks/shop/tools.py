"""The shop's tools: keep the cart up to date (Python adds it up, rule 1), place a
cash-on-delivery order, and track, change or cancel one: the order placed in this chat, or any
with its code and the buyer's mobile (D55). A delivered order can get an exchange request.
On the web chat, it can show products' photos (D57).

A shop can take the delivery charge online first (D47): then `place_order` saves the order
`unpaid` and returns a payment link. No tool can mark it paid: only the payment check can."""

from typing import Any, cast

from pydantic import BaseModel, Field

from heydesk.desks.base import NewMessage
from heydesk.desks.shared import awaiting_payment
from heydesk.desks.shop.models import ShopOrder
from heydesk.desks.shop.store import OutOfStock, ShopStore, TooLate
from heydesk.desks.shop.values import (
    CANCELLABLE,
    CHANGEABLE,
    LOW_STOCK,
    OWN,
    STATUS_WORDS,
    NewOrder,
    OrderCancel,
    OrderEdit,
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

    needed: dict[tuple[str, int], int] = {}  # (code, counter) → how many, from each count
    for (code, size, colour), qty in wanted.items():
        counter = products[code].counter(size, colour)
        if counter is not None:
            needed[(code, counter)] = needed.get((code, counter), 0) + qty
    short = []
    for (code, counter), qty in needed.items():
        product, left = products[code], products[code].count(counter)
        if qty > left:
            others = f" (left: {product.left()})" if counter != OWN and product.left() else ""
            short.append(
                f"{product.what(counter)}: "
                + (f"only {left} left" if left else "sold out")
                + others
            )
    if short:
        return fail(f"Not enough in stock. {'; '.join(short)}. Tell the visitor.")

    config = cast(ShopConfig, ctx.agent)
    currency = config.currency
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
    if 0 < subtotal < config.free_delivery_over:
        data["free_delivery_if_you_add"] = money(config.free_delivery_over - subtotal, currency)
    elif subtotal and config.free_delivery_over:
        # Said outright: with nothing said, Maira made up a nudge with no amount (13 Oct)
        data["delivery"] = "free"
    return ToolResult(ok=True, data=data, state={"cart": {"lines": lines, "subtotal": subtotal}})


UPDATE_CART = Tool(
    name="update_cart",
    description=(
        "Set the visitor's whole order: every product with its quantity, and its size and "
        "colour when it has them. Call it after every change. Checks the sizes, colours and "
        "stock, and returns each line and the subtotal; tell the visitor them exactly as given. "
        "It may also say how much more would make the delivery free, or that it's free."
    ),
    input_model=UpdateCartInput,
    run=update_cart,
    label="Updating your order…",
)


class PlaceOrderInput(BaseModel):
    area: str = Field(
        default="",
        description="Where to deliver: one of the shop's areas, or the place they named, "
        "like Mirpur or Savar",
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
    takes = f" Delivery usually takes {zone.days}." if zone.days else ""
    return ToolResult(
        ok=True,
        data={
            "code": code,
            "total": data["total"],
            "next": f"Tell them the code {code}, that the shop will call {phone} to confirm "
            f"before sending it, and that they pay {data['total']} to the rider.{takes}",
        },
        state={"cart": {}},
        alert=await _order_alert(ctx.store, code, read_back, phone, cart["lines"]),
    )


async def _order_alert(
    store: ShopStore, code: str, read_back: str, phone: str, lines: list[dict[str, Any]]
) -> str:
    """The owner's new-order alert: the order, and a warning when this mobile's orders came
    back before, and what's nearly sold out now. The visitor never hears these."""
    alert = f"🛍️ New order {code}\n{read_back}"
    trouble = await store.past_trouble(phone)
    if trouble:
        counts = ", ".join(f"{n} {status}" for status, n in sorted(trouble.items(), reverse=True))
        alert += f"\n⚠️ This mobile before: {counts}"
    products = {p.code: p for p in await store.products()}
    low: list[str] = []
    for line in lines:  # the counts this order took from, as they are now
        product = products.get(line["code"])
        counter = product.counter(line["size"], line["colour"]) if product else None
        if product is None or counter is None or product.count(counter) > LOW_STOCK:
            continue
        left = product.count(counter)
        said = f"{product.what(counter)}: " + (f"{left} left" if left else "sold out")
        if said not in low:
            low.append(said)
    if low:
        alert += f"\n📦 Running low: {'; '.join(low)}"
    return alert


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
    code: str = Field(
        default="", description="The order code, like O-104. Empty for the order made in this chat"
    )
    phone: str = Field(default="", description="The mobile number the order was placed with")


async def _find(ctx: Ctx, code: str, phone: str) -> ShopOrder | ToolResult:
    """With no code, the one order placed in this conversation (D55). Otherwise the order, if
    the code and the mobile both match: one answer for a wrong code and a wrong number, so a
    stranger can't find out whose order a code is."""
    if not code.strip():
        here = await ctx.store.orders_here()
        if len(here) == 1:
            return here[0]
        if here:
            codes = ", ".join(o.code for o in here)
            return fail(f"This chat has several orders: {codes}. Ask which one.")
        return fail(
            "Ask for the order code (like O-104), then the mobile number it was placed with."
        )
    if not phone.strip():
        return fail("Ask for the mobile number the order was placed with.")
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
        "Where an order is. For the order placed in this chat, call it with no code. Otherwise "
        "first ask for its code (like O-104), then the mobile number it was placed with. "
        "Returns its status, items, total and the courier if it has one."
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
        "Cancel an order before it's handed to the courier. For the order placed in this chat, "
        "no code is needed; otherwise ask for its code (like O-104), then the mobile number it "
        "was placed with. The first call finds it and gives what to read back; call again after "
        "a clear yes."
    ),
    input_model=FindOrderInput,
    run=cancel_order,
    label="Finding your order…",
    timeout_s=10.0,
)


class ChangeOrderInput(FindOrderInput):
    address: str = Field(default="", description="A new full address, if it changes")
    area: str = Field(
        default="", description="A new delivery area, or the place they named, if it changes"
    )
    new_phone: str = Field(default="", description="A new mobile number for the rider")
    note: str = Field(default="", description="A new note for the shop")


async def change_order(ctx: Ctx, args: ChangeOrderInput) -> ToolResult:
    """New delivery details before the courier has it (D55). Python works out a new area's
    charge and the total again; the items can't change here."""
    found = await _find(ctx, args.code, args.phone)
    if isinstance(found, ToolResult):
        return found
    if found.status not in CHANGEABLE:
        return fail(
            f"Order {found.code} is {STATUS_WORDS[found.status]}: it can't be changed now. "
            "Offer to take a message for the team."
        )
    if not any(f.strip() for f in (args.address, args.area, args.new_phone, args.note)):
        return fail(
            "Ask what they want to change: the address, the delivery area, the mobile or the "
            "note. To change the items, they cancel and order again."
        )
    config = cast(ShopConfig, ctx.agent)
    check = Checks()
    # Only what they gave is checked (an empty one is None); the rest stays as it is
    address = check.take("address", args.address, lambda said: parse.text(said, limit=300))
    zone = check.take("area", args.area, lambda said: match_zone(config.delivery, said))
    phone = check.take("new_phone", args.new_phone, mobile)
    if check.problems:
        return fail(" ".join(check.problems.values()))
    if zone and zone.name != found.zone and found.paid:
        return fail(
            f"The delivery charge for {found.zone} was paid online, so the area can't change "
            "here. Offer to take a message for the team."
        )

    def money_of(amount: int) -> str:
        return money(amount, found.currency) if amount else "free"

    charge = config.delivery_charge(zone, found.subtotal) if zone else found.delivery_charge
    edit = OrderEdit(
        number=found.number,
        address=address or found.address,
        zone=zone.name if zone else found.zone,
        delivery_charge=charge,
        phone=phone or found.phone,
        note=" ".join(args.note.split())[:500] or found.note,
    )
    changes = [
        f"{label}: {old} → {new}"
        for label, old, new in (
            ("address", found.address, edit.address),
            ("delivery area", found.zone, edit.zone),
            ("delivery", money_of(found.delivery_charge), money_of(edit.delivery_charge)),
            (
                "total",
                money(found.total, found.currency),
                money(found.subtotal + edit.delivery_charge, found.currency),
            ),
            ("mobile", found.phone, edit.phone),
            ("note", found.note or "none", edit.note or "none"),
        )
        if old != new
    ]
    if not changes:
        return fail("That's what the order already has. Ask what they want to change.")
    read_back = f"Change order {found.code}: " + "; ".join(changes)
    confirmed = await confirm(ctx.decider, edit, read_back, ctx.agent_said, ctx.visitor_said)
    if confirmed is None:
        return ask_for_yes(read_back, "change_order")
    try:
        await ctx.store.change_order(confirmed)
    except TooLate:
        return fail(f"Order {found.code} has just gone to the courier. Offer a message.")
    return ToolResult(
        ok=True,
        data={"done": read_back, "next": "Tell them the order is changed."},
        alert=f"✏️ Order {found.code} changed\n{read_back}",
    )


CHANGE_ORDER = Tool(
    name="change_order",
    description=(
        "Change an order's address, delivery area, mobile or note before the courier has it. "
        "For the order placed in this chat, no code is needed; otherwise ask for its code, "
        "then the mobile it was placed with. Give only what changes. The first call gives what "
        "to read back, with any new delivery charge and total; call again after a clear yes. "
        "The items can't change: they cancel and order again."
    ),
    input_model=ChangeOrderInput,
    run=change_order,
    label="Changing your order…",
    timeout_s=10.0,
)


class ExchangeInput(FindOrderInput):
    product: str = Field(default="", description="The product's code, shown in [brackets]")
    size: str = Field(default="", description="The size they want instead, if it changes")
    colour: str = Field(default="", description="The colour they want instead, if it changes")
    reason: str = Field(default="", description="Optional: why")


async def request_exchange(ctx: Ctx, args: ExchangeInput) -> ToolResult:
    """An exchange request on a delivered order, within the shop's days (D55). It's saved as a
    message for the team, who arrange the swap: no stock changes here."""
    days = cast(ShopConfig, ctx.agent).exchange_days
    if not days:
        return fail("The shop doesn't take exchange requests here. Offer to take a message.")
    found = await _find(ctx, args.code, args.phone)
    if isinstance(found, ToolResult):
        return found
    if found.status != "delivered":
        return fail(
            f"Order {found.code} is {STATUS_WORDS[found.status]}: an exchange is only for a "
            "delivered order."
        )
    since = (ctx.now - found.updated_at).days  # when it was marked delivered
    if since > days:
        return fail(
            f"Order {found.code} was delivered {since} days ago; exchanges are within {days} "
            "days. Tell them, and offer to take a message for the team."
        )
    line = next((line for line in found.lines if line["code"] == args.product), None)
    if line is None:
        return fail(f"Which item? The order has: {lines_text(found.lines)}. Ask which one.")
    if not (args.size.strip() or args.colour.strip()):
        return fail("Ask which size or colour they want instead.")
    product = next((p for p in await ctx.store.products() if p.code == line["code"]), None)
    if product is None:
        return fail(f"{line['name']} isn't sold any more. Offer to take a message for the team.")
    try:  # only sizes and colours the product comes in (as update_cart checks them)
        size = product.choose("size", args.size) if args.size.strip() else line["size"]
        colour = product.choose("colour", args.colour) if args.colour.strip() else line["colour"]
    except ValueError as error:
        return fail(str(error))  # "…isn't one of them. Ask which size: M, L, XL."
    counter = product.counter(size, colour)
    if counter is not None and product.count(counter) == 0:
        left = f" Left: {product.left()}." if counter != OWN and product.left() else ""
        return fail(f"{product.what(counter)} is sold out.{left} Ask what they'd like instead.")
    if (size, colour) == (line["size"], line["colour"]):
        return fail("That's the size and colour they have. Ask what they want instead.")
    reason = " ".join(args.reason.split())[:300]
    swap = {**line, "quantity": 1, "size": size, "colour": colour}
    read_back = (
        f"Exchange for order {found.code}: {lines_text([{**line, 'quantity': 1}])} for "
        f"{lines_text([swap])}" + (f"; why: {reason}" if reason else "")
    )
    message = NewMessage(read_back, found.name, found.phone)
    confirmed = await confirm(ctx.decider, message, read_back, ctx.agent_said, ctx.visitor_said)
    if confirmed is None:
        return ask_for_yes(read_back, "request_exchange")
    code = await ctx.store.save_message(confirmed)
    return ToolResult(
        ok=True,
        data={
            "code": code,
            "next": f"Tell them the team will call {found.phone} to arrange the exchange.",
        },
        alert=f"🔁 Exchange request {code}\n{read_back}\n{found.name}, {found.phone}",
    )


REQUEST_EXCHANGE = Tool(
    name="request_exchange",
    description=(
        "Ask the shop to swap an item from a delivered order for another size or colour: the "
        "product's code and the size or colour they want. For the order placed in this chat, "
        "no code is needed; otherwise ask for its code, then the mobile. It checks the order "
        "was delivered recently enough and that the product comes in that size or colour, then "
        "gives what to read back; call again after a clear yes."
    ),
    input_model=ExchangeInput,
    run=request_exchange,
    label="Noting your exchange…",
    timeout_s=10.0,
)


class ShowPhotosInput(BaseModel):
    products: list[str] = Field(
        min_length=1, max_length=3, description="Up to 3 product codes, shown in [brackets]"
    )


async def show_photos(ctx: Ctx, args: ShowPhotosInput) -> ToolResult:
    """Each product's first photo, with its name and price, on the visitor's screen (D57)."""
    if ctx.channel != "web":
        return fail("Photos can't be shown here. Describe the product from its line instead.")
    products = {p.code: p for p in await ctx.store.products()}
    unknown = [code for code in args.products if code not in products]
    if unknown:
        return fail(f"Not in the shop: {', '.join(unknown)}. Use the codes in [brackets].")
    chosen = [products[code] for code in dict.fromkeys(args.products)]
    shown = [p for p in chosen if p.photos]
    data: dict[str, Any] = {"shown": [p.name for p in shown]}
    if len(shown) < len(chosen):
        data["no_photo"] = [p.name for p in chosen if not p.photos]
        data["next"] = "Describe the ones with no photo from their line."
    else:
        data["next"] = "They can see the photos now. Ask if it's what they're looking for."
    photos = [
        {"name": p.name, "price": money(p.price, ctx.agent.currency), "url": p.photos[0]}
        for p in shown
    ]
    return ToolResult(ok=True, data=data, photos=photos or None)


SHOW_PHOTOS = Tool(
    name="show_photos",
    description=(
        "Show the visitor photos of up to 3 products, with their names and prices: to ask if a "
        "photo they sent is one of them, or when they ask to see a product."
    ),
    input_model=ShowPhotosInput,
    run=show_photos,
    label="Finding the photos…",
)

TOOLS = [
    UPDATE_CART,
    PLACE_ORDER,
    TRACK_ORDER,
    CHANGE_ORDER,
    CANCEL_ORDER,
    REQUEST_EXCHANGE,
    SHOW_PHOTOS,
]
