"""The cart: update_cart keeps the whole order so far, priced by Python (rule 1), with the
coupon, the stock check and what goes with it."""

from typing import Any, cast

from pydantic import BaseModel, Field

from heydesk.runtime.speech import money
from heydesk.runtime.tool import Tool, ToolContext, ToolResult, fail
from heydesk.shop.config import ShopConfig
from heydesk.shop.orders import (
    lines_text,
)
from heydesk.shop.products import OWN_STOCK, Product
from heydesk.shop.store import ShopStore

type Ctx = ToolContext[ShopStore]


class CartLine(BaseModel):
    code: str = Field(description="The product's code, shown in [brackets]")
    quantity: int = Field(ge=1, le=20)
    size: str = Field(default="", description="One of the product's sizes, if it has any")
    colour: str = Field(default="", description="One of the product's colours, if it has any")


class UpdateCartInput(BaseModel):
    coupon: str = Field(
        default="",
        description="A coupon code the visitor gave, like EID10. Leave it out to keep the "
        "one the order has.",
    )
    lines: list[CartLine] = Field(
        description="Every product in the order, not just the change. An empty list clears it."
    )


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


async def update_cart(ctx: Ctx, args: UpdateCartInput) -> ToolResult:
    products = {p.code: p for p in await ctx.store.products()}
    lines = lines_with_prices(products, args.lines)
    if isinstance(lines, ToolResult):
        return lines

    short = stock_problem(products, lines)
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
    cart: dict[str, Any] = {"lines": lines, "subtotal": subtotal}
    state: dict[str, Any] = {"cart": cart}
    offer = _goes_with(products, lines, ctx.state.get("offered") or [])
    if offer:  # each is offered once in a chat
        data["goes_with"] = [p.prompt_line(currency) for p in offer]
        state["offered"] = [*(ctx.state.get("offered") or []), *(p.code for p in offer)]
    kept = ((ctx.state.get("cart") or {}).get("coupon") or {}).get("code", "")
    # the buyer's code, the cart's, or an offer sent to this chat
    code = "".join(args.coupon.split()).upper() or kept or await ctx.store.offer_code()
    if not code or not subtotal:
        return ToolResult(ok=True, data=data, state=state)
    coupon = await ctx.store.coupon(code)  # Python checks it and takes it off
    problem = coupon.problem(ctx.now) if coupon else f"There's no coupon {code}."
    if coupon is None or problem:  # the order is kept, without it
        hint = f"{problem} Tell the visitor; the order is kept without a coupon."
        return ToolResult(ok=False, data=data | {"coupon_problem": hint}, state=state)
    cart["coupon"] = {"code": coupon.code, "kind": coupon.kind, "value": coupon.value}
    cart["discount"] = coupon.discount(subtotal)
    data["coupon"] = coupon.describe(currency)
    if cart["discount"]:
        data["discount"] = money(-cart["discount"], currency)
        data["subtotal_after_coupon"] = money(subtotal - cart["discount"], currency)
    if coupon.kind == "free_delivery":
        data["delivery"] = "free"
        data.pop("free_delivery_if_you_add", None)
    return ToolResult(ok=True, data=data, state=state)


def _goes_with(
    products: dict[str, Product], lines: list[dict[str, Any]], offered: list[str]
) -> list[Product]:
    """What goes with the cart's products (`Product.goes_with`): the ones not in it, not
    offered before and not sold out; at most 2."""
    in_cart = {line["code"] for line in lines}
    codes = dict.fromkeys(code for line in lines for code in products[line["code"]].goes_with)
    fits = [
        products[code]
        for code in codes
        if code in products
        and code not in in_cart
        and code not in offered
        and not products[code].sold_out()
    ]
    return fits[:2]


UPDATE_CART = Tool(
    name="update_cart",
    description=(
        "Set the visitor's whole order: every product with its quantity, and its size and "
        "colour when it has them, and a coupon code if they gave one. Call it after every "
        "change. Checks the sizes, colours, stock and coupon, and returns each line, the "
        "subtotal and any discount; tell the visitor them exactly as given. "
        "It may also say how much more would make the delivery free, or that it's free, and "
        "what goes with it (`goes_with`)."
    ),
    input_model=UpdateCartInput,
    run=update_cart,
    label="Updating your order…",
)
