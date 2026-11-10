"""The tools that look products up and show them: find_product for a long list, and
show_photos for product cards."""

from typing import Any

from pydantic import BaseModel, Field

from heydesk.runtime.speech import money
from heydesk.runtime.tool import Tool, ToolContext, ToolResult, fail
from heydesk.shop.products import MAX_FOUND_PRODUCTS, MAX_PRODUCT_CARDS, Product, find_products
from heydesk.shop.store import ShopStore

type Ctx = ToolContext[ShopStore]


class FindProductInput(BaseModel):
    group: str = Field(
        default="",
        max_length=200,
        description="One of the groups in the Products section, if you can tell which",
    )
    words: str = Field(
        default="",
        max_length=200,
        description=(
            "The other words that pick it out: a name, a brand or what it's for. Leave out "
            "sizes and colours: each product's line has them"
        ),
    )


async def find_product(ctx: Ctx, args: FindProductInput) -> ToolResult:
    """For a shop with too many products to list in the prompt: the model picks the group
    and the words, Python finds the products. Only the ones with every word are shown as
    cards (except on a call); the closest others are only told, so no wrong photos show."""
    products = await ctx.store.products()
    groups = list(dict.fromkeys(p.category for p in products))
    if args.group and args.group.lower() not in {g.lower() for g in groups}:
        return fail(f"No group {args.group!r}. The groups are: {', '.join(groups)}.")
    if not args.group and not args.words.strip():
        return fail("Give a group or some words to look for.")
    found, every = find_products(products, args.words, args.group)
    where = f"in {args.group}" if args.group else "in the shop"
    if not found:
        return fail(
            f"Nothing {where} matches {args.words!r}. Try other words or another group, or "
            "ask what it's like. Don't say the shop doesn't have it yet."
        )
    currency = ctx.agent.currency
    lines = [p.prompt_line(currency) for p in found[:MAX_FOUND_PRODUCTS]]
    if not every:  # no cards: these aren't what they asked for
        return ToolResult(
            ok=True,
            data={
                "closest": lines,
                "next": (
                    f"Nothing {where} has all of {args.words!r}. Say so, and offer these "
                    "instead; show_photos shows them if they want to see."
                ),
            },
        )
    data: dict[str, Any] = {"products": lines}
    if len(found) > MAX_FOUND_PRODUCTS:
        data["next"] = f"These are {MAX_FOUND_PRODUCTS} of {len(found)}. Ask what they'd like."
    if ctx.channel == "voice":
        return ToolResult(ok=True, data=data)
    shown = found[:MAX_FOUND_PRODUCTS]
    data["shown"] = [p.name for p in shown]  # their cards are on the screen already
    return ToolResult(ok=True, data=data, photos=product_cards(shown, currency))


def product_cards(products: list[Product], currency: str) -> list[dict[str, str]]:
    """Each product's card for the visitor's screen: its first photo (or none), name, price and
    first video. A tap on its Order button says "Order [code]"."""
    return [
        {
            "code": p.code,
            "name": p.name,
            "price": money(p.price, currency),
            "url": p.photos[0] if p.photos else "",
            "video": p.videos[0] if p.videos else "",
        }
        for p in products
    ]


FIND_PRODUCT = Tool(
    name="find_product",
    description=(
        "Look up the shop's products: pick the group they mean from the Products section, "
        "and the words that pick it out (a brand, a name). Returns up to 8 with every word, "
        "each with its code, price, sizes, colours and stock, and shows the visitor their "
        "cards (`shown`); with none, the closest (`closest`), not shown. Use it when the "
        "Products section says to."
    ),
    input_model=FindProductInput,
    run=find_product,
    label="Looking for it…",
    filler="Let me look.",
)


class ShowPhotosInput(BaseModel):
    products: list[str] = Field(
        min_length=1,
        max_length=MAX_PRODUCT_CARDS,
        description=f"Up to {MAX_PRODUCT_CARDS} product codes, shown in [brackets]",
    )


async def show_photos(ctx: Ctx, args: ShowPhotosInput) -> ToolResult:
    """Each product's card, side by side, on the visitor's screen: its first photo, name and
    price."""
    if ctx.channel == "voice":  # Messenger and Telegram send them as photos
        return fail("Photos can't be shown here. Describe the product from its line instead.")
    products = {p.code: p for p in await ctx.store.products()}
    unknown = [code for code in args.products if code not in products]
    if unknown:
        return fail(f"Not in the shop: {', '.join(unknown)}. Use the codes in [brackets].")
    chosen = [products[code] for code in dict.fromkeys(args.products)]
    data: dict[str, Any] = {"shown": [p.name for p in chosen]}
    if any(not p.photos for p in chosen):
        data["no_photo"] = [p.name for p in chosen if not p.photos]
        data["next"] = "Describe the ones with no photo from their line."
    else:
        data["next"] = "They can see the photos now. Ask if it's what they're looking for."
    if any(p.videos for p in chosen):  # the card has a Video button
        data["video"] = [p.name for p in chosen if p.videos]
    return ToolResult(ok=True, data=data, photos=product_cards(chosen, ctx.agent.currency))


SHOW_PHOTOS = Tool(
    name="show_photos",
    description=(
        f"Show the visitor up to {MAX_PRODUCT_CARDS} products side by side, each with its photo, name and "
        "price: to ask if a photo they sent is one of them, or when they ask to see a product."
    ),
    input_model=ShowPhotosInput,
    run=show_photos,
    label="Finding the photos…",
)
