"""The shop's tools: keep the cart up to date (Python adds it up, rule 1), place a
cash-on-delivery order, and track, change or cancel one: the order placed in this chat, or any
with its code and the buyer's mobile (D55). A delivered order can get an exchange request.
On the web chat, it can show products' photos (D57).

A shop can take the delivery charge online first (D47): then `place_order` saves the order
`unpaid` and returns a payment link. No tool can mark it paid: only the payment check can."""

from typing import Any, cast

from pydantic import BaseModel, Field

from heydesk.desks.base import NewMessage, TooLate
from heydesk.desks.shared import awaiting_payment
from heydesk.desks.shop.models import ShopOrder
from heydesk.desks.shop.store import OutOfStock, ShopStore
from heydesk.desks.shop.values import (
    CANCELLABLE,
    CHANGEABLE,
    LOW_STOCK,
    OWN,
    STATUS_WORDS,
    NewOrder,
    OrderCancel,
    OrderConfirm,
    OrderEdit,
    Product,
    ShopConfig,
    cart_text,
    find_products,
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


class FindProductInput(BaseModel):
    query: str = Field(
        min_length=1,
        max_length=200,
        description="What they're looking for: a name, a group, a colour or what it's for",
    )


async def find_product(ctx: Ctx, args: FindProductInput) -> ToolResult:
    """For a shop with too many products to list in the prompt (D80): the closest ones, as
    the prompt's own lines."""
    found = find_products(await ctx.store.products(), args.query)
    if not found:
        return fail(
            f"Nothing in the shop matches {args.query!r}. Try other words (a group, a colour), "
            "or ask what it's like. Don't say the shop doesn't have it yet."
        )
    currency = ctx.agent.currency
    return ToolResult(ok=True, data={"products": [p.line(currency) for p in found]})


FIND_PRODUCT = Tool(
    name="find_product",
    description=(
        "Look up the shop's products by a name, a group, a colour or what it's for. Returns up "
        "to 8, each with its code, price, sizes, colours and stock. Use it when the Products "
        "section says to."
    ),
    input_model=FindProductInput,
    run=find_product,
    label="Looking for it…",
    filler="Let me look.",
)


class CartLine(BaseModel):
    code: str = Field(description="The product's code, shown in [brackets]")
    quantity: int = Field(ge=1, le=20)
    size: str = Field(default="", description="One of the product's sizes, if it has any")
    colour: str = Field(default="", description="One of the product's colours, if it has any")


class UpdateCartInput(BaseModel):
    lines: list[CartLine] = Field(
        description="Every product in the order, not just the change. An empty list clears it."
    )


def _priced(
    products: dict[str, Product], given: list[CartLine]
) -> list[dict[str, Any]] | ToolResult:
    """The lines as an order keeps them, with the shop's prices, the same product, size and
    colour added up; or what's wrong with them. Stock isn't checked here."""
    unknown = [line.code for line in given if line.code not in products]
    if unknown:
        return fail(f"Not in the shop: {', '.join(unknown)}. Use the codes in [brackets].")

    wanted: dict[tuple[str, str, str], int] = {}  # the same product, size and colour is added up
    problems = []
    for line in given:
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
    return [
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


def _short(
    products: dict[str, Product],
    lines: list[dict[str, Any]],
    held: list[dict[str, Any]] | None = None,
) -> str:
    """What there isn't enough of for `lines`, for the model to tell the visitor; "" when
    there's enough. `held` are an order's own items, still counted as on the shelf for it."""
    needed: dict[tuple[str, int], int] = {}  # (code, counter) → how many, from each count
    for line, sign in [(line, 1) for line in lines] + [(line, -1) for line in held or []]:
        product = products.get(line["code"])
        counter = product.counter(line["size"], line["colour"]) if product else None
        if counter is not None:
            key = (line["code"], counter)
            needed[key] = needed.get(key, 0) + sign * line["quantity"]
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
    return f"Not enough in stock. {'; '.join(short)}. Tell the visitor." if short else ""


async def update_cart(ctx: Ctx, args: UpdateCartInput) -> ToolResult:
    products = {p.code: p for p in await ctx.store.products()}
    lines = _priced(products, args.lines)
    if isinstance(lines, ToolResult):
        return lines

    short = _short(products, lines)
    if short:
        return fail(short)

    config = cast(ShopConfig, ctx.agent)
    currency = config.currency
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


DISTRICT = (  # the model reads the district off the address; Python picks the area (D98)
    "The address's district, in English, even if they didn't write it: Mirpur or Uttara → "
    "Dhaka, Patenga → Chattogram, Savar → Dhaka"
)


class PlaceOrderInput(BaseModel):
    address: str = Field(
        default="",
        description="The full address: house and road, area or thana, district; a landmark "
        "if they gave one",
    )
    district: str = Field(default="", description=DISTRICT)
    name: str = Field(default="", description="The name for the order")
    phone: str = Field(default="", description="The buyer's mobile number, for the rider")
    note: str = Field(default="", description="Optional: anything the shop should know")


async def place_order(ctx: Ctx, args: PlaceOrderInput) -> ToolResult:
    cart = ctx.state.get("cart") or {}
    if not cart.get("lines"):
        return fail("There's no order yet. Take it with update_cart first.")
    config = cast(ShopConfig, ctx.agent)
    check = Checks(together=ctx.channel != "voice")  # a chat asks for it all at once (D92)
    address = check.take("address", args.address, lambda said: parse.text(said, limit=300))
    zone = (  # Python finds the area from the district (D98)
        check.take("district", args.district, lambda d: match_zone(config.delivery, d))
        if args.district.strip() or args.address.strip()
        else None  # asked for with the address
    )
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
        status=(  # one way to confirm it (D92): the advance, the shop's call, or none
            "unpaid"
            if charge and zone.pay_first and await ctx.store.pays_first(config)
            else "new"
            if config.call_to_confirm
            else "confirmed"
        ),
    )
    how = (
        f"the delivery charge {data['delivery']} paid online first, then "
        f"{money(cart['subtotal'], currency)} in cash on delivery"
        if order.status == "unpaid"
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
    if order.status == "unpaid":  # the owner hears about it once it's paid (D47)
        link = ctx.store.pay_link(saved.id)
        return awaiting_payment(code, link, data["delivery"], saved.PAY_FOR, {"cart": {}})
    takes = f" Delivery usually takes {zone.days}." if zone.days else ""
    sent = (
        f"that the shop will call {phone} to confirm it before sending it"
        if order.status == "new"
        else "that it's confirmed and will be sent out"
    )
    return ToolResult(
        ok=True,
        data={
            "code": code,
            "total": data["total"],
            "next": f"Tell them the code {code}, {sent}, and that they pay {data['total']} to "
            f"the delivery man, who calls before he comes.{takes}",
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
        "Place a cash-on-delivery order: the full address with its district, the name and the "
        "mobile number. Call it as soon as you have the "
        "address: it gives the delivery charge and the total, and says what's still needed. "
        "When it has everything it gives you what to read back; call it again with the same "
        "details after a clear yes. Returns the order code; tell it to the visitor. If the "
        "delivery charge is paid first, it returns a payment link instead, and the order waits "
        "for the payment."
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


async def _find(ctx: Ctx, code: str, phone: str, by_phone: bool = False) -> ShopOrder | ToolResult:
    """With no code, the order the shop's call is about (D88), or the one placed in this
    conversation (D55); with `by_phone`, the latest one with that mobile (D92); or this buyer's
    last one on Telegram or Messenger (D81, D92). Otherwise the order, if the code and the mobile
    both match: one answer for a wrong code and a wrong number, so a stranger can't find out
    whose order a code is."""
    calling = ctx.state.get("calling")
    if not code.strip() and calling:
        found = await ctx.store.order(calling["number"])
        if found:
            return found
    if not code.strip():
        here = await ctx.store.orders_here()
        if len(here) == 1:
            return here[0]
        if here:
            codes = ", ".join(o.code for o in here)
            return fail(f"This chat has several orders: {codes}. Ask which one.")
        if by_phone and phone.strip():  # buyers know their number, not the code
            try:
                latest = await ctx.store.latest_order(mobile(phone))
            except ValueError as error:
                return fail(str(error))
            if latest is None:
                return fail("No order was placed with that mobile number. Ask them to check it.")
            return latest
        before = await ctx.store.last_order_before()  # the same buyer, back on Telegram
        if before:
            return before
        if by_phone:
            return fail("Ask for the mobile number the order was placed with.")
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
    found = await _find(ctx, args.code, args.phone, by_phone=True)
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
    return ToolResult(ok=True, data=data)


TRACK_ORDER = Tool(
    name="track_order",
    description=(
        "Where an order is. For the order placed in this chat, or this buyer's last one, call "
        "it with nothing. Otherwise the mobile number it was placed with is enough: it finds "
        "the latest order with it; a code (like O-104) and the mobile find an older one. "
        "Returns its status, items and total."
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
    return ToolResult(
        ok=True,
        data={"done": read_back},
        state=_call_over(ctx),
        alert=f"❌ Order {found.code} cancelled",
    )


def _call_over(ctx: Ctx) -> dict[str, Any] | None:
    """On the shop's call (D89), the order is settled: the call ends after the goodbye."""
    calling = ctx.state.get("calling")
    return {"calling": {**calling, "over": True}} if calling else None


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


class ConfirmOrderInput(BaseModel):
    pass


async def confirm_order(ctx: Ctx, args: ConfirmOrderInput) -> ToolResult:
    """On the shop's confirmation call (D88): the call's first line read the order back, so
    after a clear yes to it the order is `confirmed`."""
    calling = ctx.state.get("calling")
    if not calling:
        return fail("Only the shop's call confirms an order. Say the shop will call them.")
    found = await ctx.store.order(calling["number"])
    if found is None or found.status != "new":
        status = STATUS_WORDS[found.status] if found else "gone"
        return fail(f"Order {calling['code']} is {status}: nothing to confirm. Tell them.")
    confirmed = await confirm(
        ctx.decider, OrderConfirm(found.number), calling["said"], ctx.agent_said, ctx.visitor_said
    )
    if confirmed is None:
        return ask_for_yes(calling["said"], "confirm_order")
    await ctx.store.confirm_order(confirmed)
    zones = cast(ShopConfig, ctx.agent).delivery
    zone = next((z for z in zones if z.name == found.zone), None)
    takes = f" Delivery usually takes {zone.days}." if zone and zone.days else ""
    return ToolResult(
        ok=True,
        data={
            "next": f"Thank them: order {found.code} is confirmed and will be sent out.{takes} "
            "Then say goodbye."
        },
        state=_call_over(ctx),
        alert=f"✅ Order {found.code} confirmed by the buyer on the shop's call",
    )


CONFIRM_ORDER = Tool(
    name="confirm_order",
    description=(
        "On the shop's call to confirm an order: call it when the buyer says yes to the order "
        "your first line read back. Needs nothing else."
    ),
    input_model=ConfirmOrderInput,
    run=confirm_order,
    label="Confirming your order…",
    filler="One moment.",
    timeout_s=10.0,  # the commit check is a network call
)


class ChangeOrderInput(FindOrderInput):
    address: str = Field(default="", description="A new full address, if it changes")
    district: str = Field(default="", description="With a new address: " + DISTRICT)
    new_phone: str = Field(default="", description="A new mobile number for the rider")
    note: str = Field(default="", description="A new note for the shop")
    items: list[CartLine] = Field(
        default_factory=list,
        description="Only if the items change: every product in the order, not just the change",
    )


async def change_order(ctx: Ctx, args: ChangeOrderInput) -> ToolResult:
    """New items (D92) or delivery details before the courier has it (D55). Python works out
    the subtotal, a new charge and the total again; the store swaps the stock as it saves."""
    found = await _find(ctx, args.code, args.phone)
    if isinstance(found, ToolResult):
        return found
    if found.status not in CHANGEABLE:
        return fail(
            f"Order {found.code} is {STATUS_WORDS[found.status]}: it can't be changed now. "
            "Offer to take a message for the team."
        )
    if not args.items and not any(
        f.strip() for f in (args.address, args.district, args.new_phone, args.note)
    ):
        return fail("Ask what they want to change: the items, the address, the mobile or the note.")
    if args.address.strip() and not args.district.strip():
        return fail("Give the new address's district too (Mirpur → Dhaka); ask if you can't tell.")
    config = cast(ShopConfig, ctx.agent)
    lines, subtotal = found.lines, found.subtotal
    if args.items:
        products = {p.code: p for p in await ctx.store.products()}
        priced = _priced(products, args.items)
        if isinstance(priced, ToolResult):
            return priced
        short = _short(products, priced, found.lines)
        if short:
            return fail(short)
        lines, subtotal = priced, sum(line["price"] * line["quantity"] for line in priced)
    check = Checks()
    # Only what they gave is checked (an empty one is None); the rest stays as it is
    address = check.take("address", args.address, lambda said: parse.text(said, limit=300))
    zone = check.take("district", args.district, lambda d: match_zone(config.delivery, d))
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

    where = zone or next((z for z in config.delivery if z.name == found.zone), None)
    charge = (  # a paid charge stays as it was; new items can make it free, or not
        config.delivery_charge(where, subtotal)
        if where and (zone or args.items) and not found.paid
        else found.delivery_charge
    )
    edit = OrderEdit(
        number=found.number,
        lines=lines,
        subtotal=subtotal,
        address=address or found.address,
        zone=zone.name if zone else found.zone,
        delivery_charge=charge,
        phone=phone or found.phone,
        note=" ".join(args.note.split())[:500] or found.note,
    )
    changes = [
        f"{label}: {old} → {new}"
        for label, old, new in (
            ("items", lines_text(found.lines), lines_text(lines)),
            ("address", found.address, edit.address),
            ("delivery area", found.zone, edit.zone),
            ("delivery", money_of(found.delivery_charge), money_of(edit.delivery_charge)),
            (
                "total",
                money(found.total, found.currency),
                money(subtotal + edit.delivery_charge, found.currency),
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
    except OutOfStock as error:  # sold since the read-back
        return fail(f"Not enough in stock now: {error}. Tell the visitor.")
    return ToolResult(
        ok=True,
        data={"done": read_back, "next": "Tell them the order is changed."},
        alert=f"✏️ Order {found.code} changed\n{read_back}",
    )


CHANGE_ORDER = Tool(
    name="change_order",
    description=(
        "Change an order's items, address, mobile or note before the courier has "
        "it. For the order placed in this chat, no code is needed; otherwise ask for its code, "
        "then the mobile it was placed with. Give only what changes; new items are the whole "
        "new list. The first call gives what to read back, with any new subtotal, delivery "
        "charge and total; call again after a clear yes."
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
    if ctx.channel == "voice":  # Messenger and Telegram send them as photos (D62, D63)
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
    FIND_PRODUCT,
    UPDATE_CART,
    PLACE_ORDER,
    TRACK_ORDER,
    CHANGE_ORDER,
    CANCEL_ORDER,
    CONFIRM_ORDER,
    REQUEST_EXCHANGE,
    SHOW_PHOTOS,
]
