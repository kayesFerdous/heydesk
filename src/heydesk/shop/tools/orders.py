"""The order tools: place a
cash-on-delivery order, and track, change or cancel one: the order placed in this chat, or any
with its code and the buyer's mobile. A delivered order can get an exchange request.
On the web chat, it can show products' photos.

A shop can take the delivery charge online first: then `place_order` saves the order
`unpaid` and returns a payment link. No tool can mark it paid: only the payment check can."""

from datetime import timedelta
from typing import Any, cast

from pydantic import BaseModel, Field

from heydesk.domain.values import parse_code
from heydesk.runtime import cards, parse
from heydesk.runtime.guards import ask_for_yes, confirm
from heydesk.runtime.speech import money
from heydesk.runtime.text import one_line
from heydesk.runtime.tool import ArgumentChecker, Tool, ToolContext, ToolResult, fail
from heydesk.shop.config import ShopConfig
from heydesk.shop.delivery import ADDRESS_LIMIT, Zone, match_zone, with_district
from heydesk.shop.models import ShopOrder
from heydesk.shop.orders import (
    CANCELLABLE,
    CHANGEABLE,
    STATUS_WORDS,
    STEPS,
    BuyForm,
    NewOrder,
    OrderCancel,
    OrderConfirm,
    OrderEdit,
    cart_text,
    lines_text,
    mobile,
)
from heydesk.shop.products import (
    LOW_STOCK,
    OWN_STOCK,
)
from heydesk.shop.records import NewMessage, TooLate
from heydesk.shop.store import CouponGone, OutOfStock, ShopStore
from heydesk.shop.tools.cart import CartLine, lines_with_prices, stock_problem

type Ctx = ToolContext[ShopStore]


def awaiting_payment(
    code: str,
    link: str,
    amount: str,
    pay_for: str,
    state: dict[str, Any] | None = None,
    card: dict[str, Any] | None = None,
) -> ToolResult:
    """What a writing tool returns once it saved something unpaid: a link to pay, no
    alert. Only the payment check places it; no tool can. `card`: its receipt, with the link."""
    return ToolResult(
        ok=True,
        data={
            "code": code,
            "pay_link": link,
            "next": f"Send them the link to pay {pay_for}, {amount}, by bKash, Nagad or a card. "
            "It isn't placed or "
            "confirmed until it's paid: don't say it is. A message will appear here when the "
            "payment comes in.",
        },
        state=state,
        card=card,
    )


DUPLICATE_HOURS = 6  # the same mobile and items again within this is asked about
DISTRICT = (  # the model reads the district off the address; Python picks the area
    "The address's district, in English, even if they didn't write it: Mirpur or Uttara → "
    "Dhaka, Patenga → Chattogram, Savar → Dhaka"
)


class TeamOrder(BaseModel):
    """An order the team types for a buyer who called: `POST /api/shop/orders`."""

    lines: list[CartLine] = Field(min_length=1)
    name: str
    phone: str
    address: str
    district: str
    note: str = ""


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
    again: bool = Field(
        default=False,
        description="True only after they said they want a second order of the same items",
    )


async def place_order(ctx: Ctx, args: PlaceOrderInput) -> ToolResult:
    cart = ctx.state.get("cart") or {}
    if not cart.get("lines"):
        return fail("There's no order yet. Take it with update_cart first.")
    config = cast(ShopConfig, ctx.agent)
    check = ArgumentChecker(together=ctx.channel != "voice")  # a chat asks for it all at once
    address = check.take(
        "address", args.address, lambda said: parse.text(said, limit=ADDRESS_LIMIT)
    )
    zone = (  # Python finds the area from the district
        check.take("district", args.district, lambda d: match_zone(config.delivery, d))
        if args.district.strip() or args.address.strip()
        else None  # asked for with the address
    )
    name = check.take("name", args.name, parse.name)
    phone = check.take("phone", args.phone, mobile)

    currency = config.currency
    charge = _charge(config, zone, cart) if zone else 0
    data = {  # the visitor hears the charge and total as soon as the area is known
        "delivery": money(charge, currency) if charge else "free",
        "total": money(cart["subtotal"] - cart.get("discount", 0) + charge, currency),
    }
    if zone is None or address is None or name is None or phone is None:
        missing = check.result().data
        return ToolResult(ok=False, data=(data | missing) if zone else missing)
    address = with_district(address, args.district)
    if not args.again:  # the same mobile and items a little while ago
        since = ctx.now - timedelta(hours=DUPLICATE_HOURS)
        if earlier := await ctx.store.same_order(phone, cart["lines"], since):
            return fail(
                f"{phone} already ordered the same items as {earlier.code}. Ask whether this "
                "is a second order on purpose. If yes, call place_order again with again=true; "
                f"if not, tell them {earlier.code} is placed and nothing more is needed."
            )

    order, read_back, how = await new_order(
        ctx.store, config, cart, zone, address, name, phone, args.note
    )
    rows = [  # the card's details, the same as the read-back's
        cards.row("Payment", how),
        cards.row("Address", address),
        cards.row("Name", name),
        cards.row("Mobile", phone),
    ] + ([cards.row("Note", order.note)] if order.note else [])
    total = cards.amount(order.total, currency)
    confirmed = confirm(ctx.last_read_back, order, read_back)
    if confirmed is None:
        delivery = cards.line(f"Delivery ({zone.name})", cost=cards.amount(charge, currency))
        lines = [*_card_lines(cart["lines"], currency), *_coupon_line(order), delivery]
        return ask_for_yes(
            read_back, "place_order", cards.summary("Your order", rows, lines, total)
        )
    try:
        saved = await ctx.store.place_order(confirmed)
    except OutOfStock as error:
        return fail(f"Sold out since the order was made: {error}. Tell the visitor.")
    except CouponGone as error:  # used up or ended since update_cart checked it
        return fail(f"{error} Call update_cart again without it, and tell the visitor.")
    code = saved.code
    items = cards.row("Items", lines_text(cart["lines"]))
    status = STATUS_WORDS[order.status].capitalize()
    if order.status == "unpaid":  # the owner hears about it once it's paid
        link = ctx.store.pay_link(saved.id)
        card = cards.receipt(
            "Order saved", code, status, [items, *rows], total, link, ctx.store.order_link(saved.id)
        )
        return awaiting_payment(code, link, data["delivery"], saved.PAY_FOR, {"cart": {}}, card)
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
        alert=await order_alert(ctx.store, code, read_back, phone, cart["lines"]),
        card=cards.receipt(
            "Order placed",
            code,
            status,
            [items, *rows],
            total,
            order_url=ctx.store.order_link(saved.id),
        ),
    )


async def new_order(
    store: ShopStore,
    config: ShopConfig,
    cart: dict[str, Any],
    zone: Zone,
    address: str,
    name: str,
    phone: str,
    note: str,
) -> tuple[NewOrder, str, str]:
    """The order ready for a yes, its read-back, and how it's paid: the same from the chat and
    from the product page's form."""
    currency = config.currency
    charge = _charge(config, zone, cart)
    order = NewOrder(
        lines=cart["lines"],
        subtotal=cart["subtotal"],
        delivery_charge=charge,
        currency=currency,
        zone=zone.name,
        address=address,
        name=name,
        phone=phone,
        note=one_line(note, 500),
        status=(  # one way to confirm it: the advance, the shop's call, or none
            "unpaid"
            if charge and zone.pay_first and await store.pays_first(config)
            else "new"
            if config.call_to_confirm
            else "confirmed"
        ),
        coupon=(cart.get("coupon") or {}).get("code", ""),
        discount=cart.get("discount", 0),
    )
    delivery = money(charge, currency) if charge else "free"
    how = (
        f"the delivery charge {delivery} paid online first, then "
        f"{money(order.subtotal - order.discount, currency)} in cash on delivery"
        if order.status == "unpaid"
        else "paid in cash on delivery"
    )
    read_back = (  # cart_text has the coupon
        f"{cart_text(cart, currency)}; delivery {zone.name} {delivery}; "
        f"total {money(order.total, currency)}, {how}; to {address}; "
        f"name {name}; mobile {phone}" + (f"; note: {order.note}" if order.note else "")
    )
    return order, read_back, how


async def checked_order(
    store: ShopStore,
    config: ShopConfig,
    wanted: list[CartLine],
    form: BuyForm | TeamOrder,
) -> tuple[NewOrder, str, str] | str:
    """An order typed into a form, a product page's or the team's: the order,
    its read-back and how it's paid, checked as the chat's are; or what to fix, in plain
    words."""
    products = {p.code: p for p in await store.products()}
    lines = lines_with_prices(products, wanted)
    if isinstance(lines, ToolResult):
        return "Choose a size and colour that's in stock."
    if stock_problem(products, lines):
        return "There aren't that many left. Choose fewer, or another size or colour."
    check = ArgumentChecker()
    address = check.take(
        "address", form.address, lambda said: parse.text(said, limit=ADDRESS_LIMIT)
    )
    zone = check.take("district", form.district, lambda d: match_zone(config.delivery, d))
    name = check.take("name", form.name, parse.name)
    phone = check.take("mobile number", form.phone, mobile)
    if zone is None or address is None or name is None or phone is None:
        return f"Please check your {', '.join([*check.problems, *check.missing])}."
    address = with_district(address, form.district)
    cart = {"lines": lines, "subtotal": sum(li["price"] * li["quantity"] for li in lines)}
    return await new_order(store, config, cart, zone, address, name, phone, form.note)


def _charge(config: ShopConfig, zone: Zone, cart: dict[str, Any]) -> int:
    """The delivery charge: free over the shop's amount, or with a free-delivery coupon."""
    if (cart.get("coupon") or {}).get("kind") == "free_delivery":
        return 0
    return config.delivery_charge(zone, cart["subtotal"])


def _coupon_line(order: NewOrder) -> list[dict[str, Any]]:
    """The coupon on a card: "Coupon EID10", −৳370."""
    if not order.discount:
        return []
    return [
        cards.line(f"Coupon {order.coupon}", cost=cards.amount(-order.discount, order.currency))
    ]


def _card_lines(lines: list[dict[str, Any]], currency: str) -> list[dict[str, Any]]:
    """An order's items as a card's lines: "Cotton Panjabi (L, White)", 2, ৳3,700."""
    found = []
    for item in lines:
        picked = ", ".join(p for p in (item["size"], item["colour"]) if p)
        label = item["name"] + (f" ({picked})" if picked else "")
        cost = cards.amount(item["price"] * item["quantity"], currency)
        found.append(cards.line(label, item["quantity"], cost))
    return found


async def order_alert(
    store: ShopStore, code: str, read_back: str, phone: str, lines: list[dict[str, Any]]
) -> str:
    """The owner's new-order alert: the order, and a warning when this mobile's orders came
    back before, and what's nearly sold out now. The visitor never hears these."""
    alert = f"🛍️ New order {code}\n{read_back}"
    trouble = await store.return_history(phone)
    if trouble:
        counts = ", ".join(f"{n} {status}" for status, n in sorted(trouble.items(), reverse=True))
        alert += f"\n⚠️ This mobile before: {counts}"
    products = {p.code: p for p in await store.products()}
    low: list[str] = []
    for line in lines:  # the counts this order took from, as they are now
        product = products.get(line["code"])
        stock_index = product.stock_index(line["size"], line["colour"]) if product else None
        if product is None or stock_index is None or product.stock_at(stock_index) > LOW_STOCK:
            continue
        left = product.stock_at(stock_index)
        said = f"{product.display_name(stock_index)}: " + (f"{left} left" if left else "sold out")
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
    timeout_seconds=10.0,  # the commit check is a network call
)


class FindOrderInput(BaseModel):
    code: str = Field(
        default="", description="The order code, like O-104. Empty for the order made in this chat"
    )
    phone: str = Field(default="", description="The mobile number the order was placed with")


async def _find_order(
    ctx: Ctx, code: str, phone: str, by_phone: bool = False
) -> ShopOrder | ToolResult:
    """With no code, the order the shop's call is about, or the one placed in this
    conversation; with `by_phone`, the latest one with that mobile; or this buyer's
    last one on Telegram or Messenger. Otherwise the order, if the code and the mobile
    both match: one answer for a wrong code and a wrong number, so a stranger can't find out
    whose order a code is."""
    calling = ctx.state.get("calling")
    if not code.strip() and calling:
        found = await ctx.store.order(calling["number"])
        if found:
            return found
    if not code.strip():
        here = await ctx.store.orders_in_conversation()
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
        before = await ctx.store.earlier_records(ShopOrder)  # the same buyer, back again
        if before:
            return before[0]
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
    found = await _find_order(ctx, args.code, args.phone, by_phone=True)
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
    if found.status == "shipped" and found.courier_status:  # the courier's own words
        data["courier_says"] = found.courier_status
    takes = data.get("usually_takes")
    note = data["status"].capitalize() + (f". Delivery usually takes {takes}." if takes else "")
    card = cards.tracking(found.code, STEPS, found.status, note)
    return ToolResult(ok=True, data=data, card=card)


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
    found = await _find_order(ctx, args.code, args.phone)
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
    confirmed = confirm(ctx.last_read_back, OrderCancel(found.number), read_back)
    if confirmed is None:
        return ask_for_yes(read_back, "cancel_order")
    await ctx.store.cancel_order(confirmed)
    return ToolResult(
        ok=True,
        data={"done": read_back},
        state=_end_call_after_goodbye(ctx),
        alert=f"❌ Order {found.code} cancelled",
    )


def _end_call_after_goodbye(ctx: Ctx) -> dict[str, Any] | None:
    """On the shop's call, the order is settled: the call ends after the goodbye."""
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
    timeout_seconds=10.0,
)


class ConfirmOrderInput(BaseModel):
    pass


async def confirm_order(ctx: Ctx, args: ConfirmOrderInput) -> ToolResult:
    """On the shop's confirmation call: the call's first line read the order back, so
    after a clear yes to it the order is `confirmed`."""
    calling = ctx.state.get("calling")
    if not calling:
        return fail("Only the shop's call confirms an order. Say the shop will call them.")
    found = await ctx.store.order(calling["number"])
    if found is None or found.status != "new":
        status = STATUS_WORDS[found.status] if found else "gone"
        return fail(f"Order {calling['code']} is {status}: nothing to confirm. Tell them.")
    confirmed = confirm(ctx.last_read_back, OrderConfirm(found.number), calling["said"])
    if confirmed is None:
        return ask_for_yes(calling["said"], "confirm_order")
    await ctx.store.confirm_order(confirmed)
    zones = cast(ShopConfig, ctx.agent).delivery
    zone = next((z for z in zones if z.name == found.zone), None)
    takes = f" Delivery usually takes {zone.days}." if zone and zone.days else ""
    return ToolResult(
        ok=True,
        data={
            "next": f"Thank them: it's confirmed and will be sent out.{takes} Then say goodbye, "
            "warmly and briefly. Don't say the order code."
        },
        state=_end_call_after_goodbye(ctx),
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
    timeout_seconds=10.0,  # the commit check is a network call
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
    """New items or delivery details before the courier has it. Python works out
    the subtotal, a new charge and the total again; the store swaps the stock as it saves."""
    found = await _find_order(ctx, args.code, args.phone)
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
        priced = lines_with_prices(products, args.items)
        if isinstance(priced, ToolResult):
            return priced
        short = stock_problem(products, priced, found.lines)
        if short:
            return fail(short)
        lines, subtotal = priced, sum(line["price"] * line["quantity"] for line in priced)
    check = ArgumentChecker()
    # Only what they gave is checked (an empty one is None); the rest stays as it is
    address = check.take(
        "address", args.address, lambda said: parse.text(said, limit=ADDRESS_LIMIT)
    )
    zone = check.take("district", args.district, lambda d: match_zone(config.delivery, d))
    phone = check.take("new_phone", args.new_phone, mobile)
    if check.problems:
        return fail(" ".join(check.problems.values()))
    if address:  # its district is required above
        address = with_district(address, args.district)
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
        note=one_line(args.note, 500) or found.note,
    )
    changes = [
        (label, old, new)
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
    title = f"Change order {found.code}"
    read_back = f"{title}: " + "; ".join(f"{label}: {old} → {new}" for label, old, new in changes)
    confirmed = confirm(ctx.last_read_back, edit, read_back)
    if confirmed is None:
        rows = [cards.row(label.capitalize(), f"{old} → {new}") for label, old, new in changes]
        new_lines = _card_lines(lines, found.currency) if args.items else None
        total = cards.amount(subtotal + edit.delivery_charge, found.currency)
        return ask_for_yes(read_back, "change_order", cards.summary(title, rows, new_lines, total))
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
    timeout_seconds=10.0,
)


class ExchangeInput(FindOrderInput):
    product: str = Field(default="", description="The product's code, shown in [brackets]")
    size: str = Field(default="", description="The size they want instead, if it changes")
    colour: str = Field(default="", description="The colour they want instead, if it changes")
    reason: str = Field(default="", description="Optional: why")


async def request_exchange(ctx: Ctx, args: ExchangeInput) -> ToolResult:
    """An exchange request on a delivered order, within the shop's days. It's saved as a
    message for the team, who arrange the swap: no stock changes here."""
    days = cast(ShopConfig, ctx.agent).exchange_days
    if not days:
        return fail("The shop doesn't take exchange requests here. Offer to take a message.")
    found = await _find_order(ctx, args.code, args.phone)
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
        size = product.match_option("size", args.size) if args.size.strip() else line["size"]
        colour = (
            product.match_option("colour", args.colour) if args.colour.strip() else line["colour"]
        )
    except ValueError as error:
        return fail(str(error))  # "…isn't one of them. Ask which size: M, L, XL."
    stock_index = product.stock_index(size, colour)
    if stock_index is not None and product.stock_at(stock_index) == 0:
        left = (
            f" Left: {product.available_variants()}."
            if stock_index != OWN_STOCK and product.available_variants()
            else ""
        )
        return fail(
            f"{product.display_name(stock_index)} is sold out.{left} Ask what they'd like instead."
        )
    if (size, colour) == (line["size"], line["colour"]):
        return fail("That's the size and colour they have. Ask what they want instead.")
    reason = one_line(args.reason, 300)
    swap = {**line, "quantity": 1, "size": size, "colour": colour}
    read_back = (
        f"Exchange for order {found.code}: {lines_text([{**line, 'quantity': 1}])} for "
        f"{lines_text([swap])}" + (f"; why: {reason}" if reason else "")
    )
    message = NewMessage(read_back, found.name, found.phone)
    confirmed = confirm(ctx.last_read_back, message, read_back)
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
    timeout_seconds=10.0,
)
