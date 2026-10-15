"""The restaurant's tools: keep the order up to date (Python adds it up, rule 1), place it,
and track, change or cancel it: the order placed in this chat, or any with its code and the
phone (D65). When the restaurant takes bookings, book a table (Python counts the seats) or
cancel one."""

from datetime import datetime
from typing import cast

from pydantic import BaseModel, Field

from heydesk.desks.base import TooLate
from heydesk.desks.restaurant.models import Booking, Order
from heydesk.desks.restaurant.store import RestaurantStore
from heydesk.desks.restaurant.values import (
    CANCELLABLE,
    CANCELLABLE_BOOKING,
    CHANGEABLE,
    STATUS_WORDS,
    BookingCancel,
    NewBooking,
    NewOrder,
    OrderCancel,
    OrderEdit,
    RestaurantConfig,
    cart_text,
    free_times,
    has_room,
    party,
)
from heydesk.desks.shared import awaiting_payment
from heydesk.domain.values import parse_code
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
    delivery: bool = Field(
        default=False, description="True for delivery, false for pickup. Ask which, if we deliver"
    )
    time: str = Field(
        default="", description="When they'll pick it up, or want it delivered, today: 7:30 pm"
    )
    address: str = Field(default="", description="For delivery: the full address, with a landmark")
    name: str = Field(default="", description="The name for the order")
    phone: str = Field(default="", description="The visitor's phone number")
    note: str = Field(default="", description="Optional: anything the kitchen should know")


async def place_order(ctx: Ctx, args: PlaceOrderInput) -> ToolResult:
    cart = ctx.state.get("cart") or {}
    if not cart.get("lines"):
        return fail("There's no order yet. Take it with update_order first.")
    config = cast(RestaurantConfig, ctx.agent)
    if args.delivery and not config.delivers:
        return fail("We don't deliver: orders are for pickup only. Tell the visitor.")
    now = ctx.local_now
    check = Checks()
    at = check.take("time", args.time, lambda said: parse.time_today(said, now, config.hours))
    address = (
        check.take("address", args.address, lambda said: parse.text(said, limit=300))
        if args.delivery
        else ""
    )
    name = check.take("name", args.name, parse.name)
    phone = check.take("phone", args.phone, parse.phone)
    if at is None or address is None or name is None or phone is None:
        return check.result()
    note = " ".join(args.note.split())[:500]

    currency = config.currency
    charge = config.delivery_charge if args.delivery else 0
    order = NewOrder(
        lines=cart["lines"],
        food=cart["total"],
        currency=currency,
        pickup_at=datetime.combine(now.date(), at, tzinfo=now.tzinfo),
        name=name,
        phone=phone,
        note=note,
        address=address,
        delivery_charge=charge,
        pay_first=charge > 0 and await ctx.store.pays_first(config),
    )
    total = money(order.total, currency)
    if not args.delivery:
        how = f"pickup today at {clock_time(at)}"
    elif order.pay_first:
        how = (
            f"delivery to {address} today at {clock_time(at)}; delivery "
            f"{money(charge, currency)} paid online now; total {total}, the rest "
            f"{money(order.food, currency)} paid on delivery"
        )
    else:
        delivery = money(charge, currency) if charge else "free"
        how = f"delivery to {address} today at {clock_time(at)}; delivery {delivery}; total {total}"
    read_back = f"{cart_text(cart, currency)}; {how}; name {name}; phone {phone}" + (
        f"; note: {note}" if note else ""
    )
    confirmed = await confirm(ctx.decider, order, read_back, ctx.agent_said, ctx.visitor_said)
    if confirmed is None:
        return ask_for_yes(read_back, "place_order")
    saved = await ctx.store.place_order(confirmed)
    if order.pay_first:  # the owner hears about it once it's paid (D48)
        link = ctx.store.pay_link(saved.id)
        fee = money(charge, currency)
        return awaiting_payment(saved.code, link, fee, saved.PAY_FOR, {"cart": {}})
    data = {
        "order": saved.code,
        "total": total,
        "time": f"today, {clock_time(at)}",
    }
    alert = f"🛒 New order {saved.code}\n{read_back}"
    return ToolResult(ok=True, data=data, state={"cart": {}}, alert=alert)


PLACE_ORDER = Tool(
    name="place_order",
    description=(
        "Place the order: pickup or delivery, the time today, the address for a delivery, the "
        "name and the phone. Call it as soon as you have the time: it checks we're open then "
        "and says what's still needed. When it has everything it gives you what to read back; "
        "call it again with the same details after a clear yes. Returns the order code; "
        "tell it to the visitor. If the restaurant takes the delivery charge first, it returns "
        "a payment link instead."
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
    phone: str = Field(default="", description="The phone number the order was placed with")


async def _find(ctx: Ctx, code: str, phone: str) -> Order | ToolResult:
    """With no code, the one order placed in this conversation (as the shop, D55). Otherwise
    the order, if the code and the phone both match: one answer for a wrong code and a wrong
    phone, so a stranger can't find out whose order a code is."""
    if not code.strip():
        here = await ctx.store.orders_here()
        if len(here) == 1:
            return here[0]
        if here:
            codes = ", ".join(o.code for o in here)
            return fail(f"This chat has several orders: {codes}. Ask which one.")
        return fail("Ask for the order code (like O-104), then the phone it was placed with.")
    if not phone.strip():
        return fail("Ask for the phone number the order was placed with.")
    parsed = parse_code(code)
    if parsed is None or parsed[0] not in ("", "O"):
        return fail("That isn't an order code: they look like O-104. Ask again.")
    try:
        number = parse.phone(phone)
    except ValueError as error:
        return fail(str(error))
    found = await ctx.store.order(parsed[1])
    if found is None or found.phone != number:
        return fail("No order matches that code and phone number. Ask them to check both.")
    return found


def _when(ctx: Ctx, order: Order) -> str:
    return clock_time(order.pickup_at.astimezone(ctx.local_now.tzinfo).time())


async def track_order(ctx: Ctx, args: FindOrderInput) -> ToolResult:
    found = await _find(ctx, args.code, args.phone)
    if isinstance(found, ToolResult):
        return found
    data = {
        "order": found.code,
        "status": STATUS_WORDS[found.status],
        "dishes": found.about(),
        "total": money(found.total, found.currency),
        "time": _when(ctx, found),
    }
    if found.paid:
        data["paid_online"] = money(found.paid, found.currency)
    if found.status == "unpaid":
        data["pay_link"] = ctx.store.pay_link(found.id)
    return ToolResult(ok=True, data=data)


TRACK_ORDER = Tool(
    name="track_order",
    description=(
        "Where an order is. For the order placed in this chat, call it with no code. Otherwise "
        "first ask for its code (like O-104), then the phone number it was placed with. "
        "Returns its status, dishes, total and time."
    ),
    input_model=FindOrderInput,
    run=track_order,
    label="Finding your order…",
    filler="Let me look.",
)


class ChangeOrderInput(FindOrderInput):
    time: str = Field(default="", description="A new time today, if it changes: 8 pm")
    address: str = Field(default="", description="A new full address, for a delivery")
    note: str = Field(default="", description="A new note for the kitchen")


async def change_order(ctx: Ctx, args: ChangeOrderInput) -> ToolResult:
    """A new time, address or note while the order is new (D65). The dishes can't change here."""
    found = await _find(ctx, args.code, args.phone)
    if isinstance(found, ToolResult):
        return found
    if found.status not in CHANGEABLE:
        return fail(
            f"Order {found.code} is {STATUS_WORDS[found.status]}: it can't be changed now. "
            "Offer to take a message for the team."
        )
    if not any(f.strip() for f in (args.time, args.address, args.note)):
        return fail(
            "Ask what they want to change: the time, the address or the note. To change the "
            "dishes, they cancel and order again."
        )
    if args.address.strip() and not found.address:
        return fail(
            "It's a pickup order, so there's no address. To have it delivered, they "
            "cancel and order again."
        )
    now = ctx.local_now
    check = Checks()
    # Only what they gave is checked (an empty one is None); the rest stays as it is
    at = check.take("time", args.time, lambda said: parse.time_today(said, now, ctx.agent.hours))
    address = check.take("address", args.address, lambda said: parse.text(said, limit=300))
    if check.problems:
        return fail(" ".join(check.problems.values()))

    edit = OrderEdit(
        number=found.number,
        pickup_at=datetime.combine(now.date(), at, tzinfo=now.tzinfo) if at else found.pickup_at,
        address=address or found.address,
        note=" ".join(args.note.split())[:500] or found.note,
    )
    changes = [
        f"{label}: {old} → {new}"
        for label, old, new in (
            ("time", _when(ctx, found), clock_time(at) if at else _when(ctx, found)),
            ("address", found.address, edit.address),
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
        return fail(f"Order {found.code} is ready by now, so it can't change. Offer a message.")
    return ToolResult(
        ok=True,
        data={"done": read_back, "next": "Tell them the order is changed."},
        alert=f"✏️ Order {found.code} changed\n{read_back}",
    )


CHANGE_ORDER = Tool(
    name="change_order",
    description=(
        "Change an order's time (today), address (for a delivery) or note, before it's ready. "
        "For the order placed in this chat, no code is needed; otherwise ask for its code, "
        "then the phone it was placed with. Give only what changes. The first call gives what "
        "to read back; call again after a clear yes. The dishes can't change: they cancel and "
        "order again."
    ),
    input_model=ChangeOrderInput,
    run=change_order,
    label="Changing your order…",
    timeout_s=10.0,
)


async def cancel_order(ctx: Ctx, args: FindOrderInput) -> ToolResult:
    found = await _find(ctx, args.code, args.phone)
    if isinstance(found, ToolResult):
        return found
    if found.status not in CANCELLABLE:
        return fail(
            f"Order {found.code} is {STATUS_WORDS[found.status]}: it can't be cancelled now. "
            "Offer to take a message for the team."
        )
    read_back = (
        f"Cancel order {found.code}: {found.about()}, total {money(found.total, found.currency)}"
    )
    confirmed = await confirm(
        ctx.decider, OrderCancel(found.number), read_back, ctx.agent_said, ctx.visitor_said
    )
    if confirmed is None:
        return ask_for_yes(read_back, "cancel_order")
    await ctx.store.cancel_order(confirmed)
    alert = f"❌ Order {found.code} cancelled"
    if found.paid:  # the owner refunds it in SSLCommerz's panel (design §12)
        alert += f". It was paid {money(found.paid, found.currency)} online: refund it."
    return ToolResult(ok=True, data={"done": read_back}, alert=alert)


CANCEL_ORDER = Tool(
    name="cancel_order",
    description=(
        "Cancel an order before it's ready. For the order placed in this chat, no code is "
        "needed; otherwise ask for its code (like O-104), then the phone it was placed with. "
        "The first call finds it and gives what to read back; call again after a clear yes."
    ),
    input_model=FindOrderInput,
    run=cancel_order,
    label="Finding your order…",
    timeout_s=10.0,
)


class BookTableInput(BaseModel):
    people: int = Field(default=0, ge=0, le=100, description="How many people, children too")
    time: str = Field(default="", description="The day and time, like tomorrow at 8 pm")
    name: str = Field(default="", description="The name for the booking")
    phone: str = Field(default="", description="The visitor's phone number")
    note: str = Field(default="", description="Optional: a birthday, a high chair, …")


async def book_table(ctx: Ctx, args: BookTableInput) -> ToolResult:
    """Python checks the party size, the time and the seats (rule 1). A full slot is said
    as soon as the people and the time are known, before the name and phone are asked."""
    config = cast(RestaurantConfig, ctx.agent)
    now = ctx.local_now
    check = Checks()
    people = check.take(
        "people", str(args.people or ""), lambda said: party(int(said), config.max_party)
    )
    at = check.take("time", args.time, lambda said: parse.day_and_time(said, now, config.hours))
    if people and at:
        taken = await ctx.store.seats_taken(at)
        if not has_room(taken, at, people, config.seats, config.table_minutes):
            times = free_times(taken, at, people, config, now)
            offer = (
                f"Free times nearby: {', '.join(clock_time(t.time()) for t in times)}. Offer them."
                if times
                else "Nothing is free near then that day. Ask for another day or time."
            )
            return fail(f"We're full for {people} at {parse.day_time(at)}. {offer}")
    name = check.take("name", args.name, parse.name)
    phone = check.take("phone", args.phone, parse.phone)
    if people is None or at is None or name is None or phone is None:
        return check.result()
    note = " ".join(args.note.split())[:300]

    booking = NewBooking(at=at, people=people, name=name, phone=phone, note=note)
    read_back = f"Table for {people} on {parse.day_time(at)}; name {name}; phone {phone}" + (
        f"; note: {note}" if note else ""
    )
    confirmed = await confirm(ctx.decider, booking, read_back, ctx.agent_said, ctx.visitor_said)
    if confirmed is None:
        return ask_for_yes(read_back, "book_table")
    saved = await ctx.store.book_table(confirmed)
    data = {
        "booking": saved.code,
        "next": "Tell them the booking code, and that the restaurant will confirm it here.",
    }
    return ToolResult(ok=True, data=data, alert=f"🍽️ New booking {saved.code}\n{read_back}")


BOOK_TABLE = Tool(
    name="book_table",
    description=(
        "Book a table: how many people, the day and time, the name and the phone. Call it as "
        "soon as you have the people and the time: it checks we have room then, and gives "
        "other times when we're full. When it has everything it gives you what to read back; "
        "call it again with the same details after a clear yes. Returns the booking code."
    ),
    input_model=BookTableInput,
    run=book_table,
    label="Checking for a table…",
    filler="One moment.",
    timeout_s=10.0,  # the commit check is a network call
)


class CancelBookingInput(BaseModel):
    code: str = Field(default="", description="The booking code the visitor gave, like T-101")
    name: str = Field(default="", description="The name the visitor says it's under")


def _same_name(a: str, b: str) -> bool:
    return " ".join(a.casefold().split()) == " ".join(b.casefold().split())


async def _find_booking(ctx: Ctx, code: str, name: str) -> Booking | ToolResult:
    """The booking, if the code and the name both match. One answer for a wrong code and a
    wrong name, so a stranger can't find out whose booking a code is (as the clinic, D38)."""
    if not code.strip() or not name.strip():
        return fail("Ask for the booking code (like T-101), then the name it's under.")
    parsed = parse_code(code)
    if parsed is None or parsed[0] not in ("", "T"):
        return fail("That isn't a booking code: they look like T-101. Ask again.")
    found = await ctx.store.booking(parsed[1])
    if found is None or not _same_name(found.name, name):
        return fail("No booking matches that code and name. Ask them to check both.")
    return found


async def cancel_booking(ctx: Ctx, args: CancelBookingInput) -> ToolResult:
    found = await _find_booking(ctx, args.code, args.name)
    if isinstance(found, ToolResult):
        return found
    if found.status not in CANCELLABLE_BOOKING:
        status = found.status.replace("_", " ")
        return fail(f"Booking {found.code} is {status}: it can't be cancelled now.")
    when = parse.day_time(found.at.astimezone(ctx.local_now.tzinfo))
    read_back = f"Cancel booking {found.code}: table for {found.people} on {when}"
    cancel = BookingCancel(found.number)
    confirmed = await confirm(ctx.decider, cancel, read_back, ctx.agent_said, ctx.visitor_said)
    if confirmed is None:
        return ask_for_yes(read_back, "cancel_booking")
    await ctx.store.cancel_booking(confirmed)
    return ToolResult(ok=True, data={"done": read_back}, alert=f"❌ Booking {found.code} cancelled")


CANCEL_BOOKING = Tool(
    name="cancel_booking",
    description=(
        "Cancel a table booking. First ask for its code (like T-101), then the name it's under. "
        "The first call finds it and gives what to read back; call again after a clear yes."
    ),
    input_model=CancelBookingInput,
    run=cancel_booking,
    label="Finding your booking…",
    timeout_s=10.0,
)

TOOLS = [UPDATE_ORDER, PLACE_ORDER, TRACK_ORDER, CHANGE_ORDER, CANCEL_ORDER]
BOOKING_TOOLS = [BOOK_TABLE, CANCEL_BOOKING]  # only when the restaurant takes bookings
