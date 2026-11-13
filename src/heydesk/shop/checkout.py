"""Pricing and checking an order, wherever it comes from: the chat's tools, a product
page's *Buy now* form, or the team typing one for a buyer who called. Python does the facts
here (rule 1): the prices, the stock, the delivery charge and the total, and the words read
back before a yes. Saving it is the store's job (`shop/store.py`)."""

from typing import Any

from pydantic import BaseModel, Field

from heydesk.runtime import parse
from heydesk.runtime.speech import money, money_or_free
from heydesk.runtime.text import one_line
from heydesk.runtime.tool import ArgumentChecker, ToolResult, fail
from heydesk.shop.config import ShopConfig
from heydesk.shop.delivery import ADDRESS_LIMIT, Zone, match_zone, with_district
from heydesk.shop.orders import BuyForm, NewOrder, cart_text, parse_mobile, subtotal_of
from heydesk.shop.products import LOW_STOCK, OWN_STOCK, Product
from heydesk.shop.store import ShopStore


class CartLine(BaseModel):
    code: str = Field(description="The product's code, shown in [brackets]")
    quantity: int = Field(ge=1, le=20)
    size: str = Field(default="", description="One of the product's sizes, if it has any")
    colour: str = Field(default="", description="One of the product's colours, if it has any")


class TeamOrder(BaseModel):
    """An order the team types for a buyer who called: `POST /api/shop/orders`."""

    lines: list[CartLine] = Field(min_length=1)
    name: str
    phone: str
    address: str
    district: str
    note: str = ""


def lines_with_prices(
    products: dict[str, Product], given: list[CartLine]
) -> list[dict[str, Any]] | ToolResult:
    """The lines as an order keeps them, with the shop's prices, the same product, size and
    colour added up; or what's wrong with them. Stock isn't checked here."""
    unknown = [line.code for line in given if line.code not in products]
    if unknown:
        return fail(f"Not in the shop: {', '.join(unknown)}. Use the codes in [brackets].")

    wanted: dict[tuple[str, str, str], int] = {}  # the same product, size and colour is added up
    problems = []
    choices: list[str] = []  # buttons for the first question: what's in stock
    for line in given:
        product = products[line.code]
        size = None
        try:
            size = product.match_option("size", line.size)
            key = (line.code, size, product.match_option("colour", line.colour))
        except ValueError as error:
            problems.append(str(error))
            choices = choices or (
                product.in_stock("size", colour=line.colour)
                if size is None
                else product.in_stock("colour", size=size)
            )
            continue
        wanted[key] = wanted.get(key, 0) + line.quantity
    if problems:
        return ToolResult(ok=False, data={"error": " ".join(problems)}, choices=choices)
    return [
        {
            "code": code,
            "name": products[code].name,
            "size": size,
            "colour": colour,
            "quantity": quantity,
            "price": products[code].price,
        }
        for (code, size, colour), quantity in wanted.items()
    ]


def stock_problem(
    products: dict[str, Product],
    lines: list[dict[str, Any]],
    held: list[dict[str, Any]] | None = None,
) -> str:
    """What there isn't enough of for `lines`, for the model to tell the visitor; "" when
    there's enough. `held` are an order's own items, still counted as on the shelf for it."""
    needed: dict[tuple[str, int], int] = {}  # (code, counter) → how many, from each count
    for line, sign in [(line, 1) for line in lines] + [(line, -1) for line in held or []]:
        product = products.get(line["code"])
        stock_index = product.stock_index(line["size"], line["colour"]) if product else None
        if stock_index is not None:
            key = (line["code"], stock_index)
            needed[key] = needed.get(key, 0) + sign * line["quantity"]
    short = []
    for (code, stock_index), quantity in needed.items():
        product, left = products[code], products[code].stock_at(stock_index)
        if quantity > left:
            others = (
                f" (left: {product.available_variants()})"
                if stock_index != OWN_STOCK and product.available_variants()
                else ""
            )
            short.append(
                f"{product.display_name(stock_index)}: "
                + (f"only {left} left" if left else "sold out")
                + others
            )
    return f"Not enough in stock. {'; '.join(short)}. Tell the visitor." if short else ""


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
    charge = cart_delivery_charge(config, zone, cart)
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
            if charge and zone.pay_first and await store.takes_payment_first(config)
            else "new"
            if config.call_to_confirm
            else "confirmed"
        ),
        coupon=(cart.get("coupon") or {}).get("code", ""),
        discount=cart.get("discount", 0),
    )
    delivery = money_or_free(charge, currency)
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
    phone = check.take("mobile number", form.phone, parse_mobile)
    if zone is None or address is None or name is None or phone is None:
        return f"Please check your {', '.join([*check.problems, *check.missing])}."
    address = with_district(address, form.district)
    cart = {"lines": lines, "subtotal": subtotal_of(lines)}
    return await new_order(store, config, cart, zone, address, name, phone, form.note)


def cart_delivery_charge(config: ShopConfig, zone: Zone, cart: dict[str, Any]) -> int:
    """The delivery charge: free over the shop's amount, or with a free-delivery coupon."""
    if (cart.get("coupon") or {}).get("kind") == "free_delivery":
        return 0
    return config.delivery_charge(zone, cart["subtotal"])


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
