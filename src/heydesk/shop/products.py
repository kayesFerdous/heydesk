"""A shop's products: their shape, their stock, how the prompt and find_product see them, and
how a list from somewhere else (a CSV, a sheet, Woo) is merged into ours. Pure."""

import re
from typing import Any, Self

from pydantic import BaseModel, Field, field_validator, model_validator

from heydesk.runtime.speech import money

LOW_STOCK = 3  # at this many or fewer, the prompt says how many are left and the owner hears
OWN_STOCK = -1  # `Product.stock_index`: the product's own stock, not a variant's
LONG_PRODUCT_LIST = 60  # more products than this, and the prompt lists the groups, not each one
MAX_FOUND_PRODUCTS = 8  # the most products find_product returns
MAX_PRODUCT_CARDS = 10  # the most product cards in a row: Messenger's carousel holds 10
FIELDS_KEPT_ON_IMPORT = (
    "photos",
    "videos",
    "goes_with",
)  # what an import leaves when its product has none


class Variant(BaseModel):
    """How many of one size and colour are left. An empty size or colour means any of
    them: `{"size": "L", "colour": "", "stock": 4}` counts L in every colour."""

    size: str = Field(default="", max_length=50)
    colour: str = Field(default="", max_length=50)
    stock: int = Field(ge=0)

    def label(self) -> str:
        """ "L/White", "L" or "White"."""
        return "/".join(part for part in (self.size, self.colour) if part)

    def matches(self, size: str, colour: str) -> bool:
        return self.size in ("", size) and self.colour in ("", colour)


class Product(BaseModel):
    code: str = Field(min_length=1, max_length=50)  # the short id the model uses: "panjabi-white"
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    price: int = Field(ge=0)  # minor units (paisa)
    was_price: int | None = Field(default=None, ge=0)  # before a sale; None: not on sale
    category: str = "Products"
    sizes: list[str] = []  # "M", "L", "XL": the visitor picks one, when there are any
    colours: list[str] = []
    stock: int | None = Field(default=None, ge=0)  # None: not counted; 0: sold out
    # Stock per size and colour. When there are any, they hold the stock and `stock` is
    # None; a size and colour no variant matches isn't counted
    variants: list[Variant] = []
    # Up to 3 photo URLs, from `POST /api/products/photos`; the first is the one the agent shows.
    # Read from a list (a CSV, a sheet, Woo) they're its links, until the store takes them
    photos: list[str] = Field(default=[], max_length=3)
    # Up to 3 video links (a YouTube or Facebook video, a how-to); its card links the first
    videos: list[str] = Field(default=[], max_length=3)
    # Up to 3 codes of products that go with it; update_cart offers them once
    goes_with: list[str] = Field(default=[], max_length=3)

    @field_validator("videos")
    @classmethod
    def _links(cls, videos: list[str]) -> list[str]:
        for link in videos:
            if not link.startswith(("http://", "https://")):
                raise ValueError(f"{link!r} isn't a link: it starts with https://")
        return videos

    @model_validator(mode="after")
    def _variants_fit(self) -> Self:
        """Each variant names one of the product's own sizes and colours, spelled its way."""
        for v in self.variants:
            for what, said in (("size", v.size), ("colour", v.colour)):
                choices: list[str] = getattr(self, what + "s")
                spelled = next((c for c in choices if c.lower() == said.lower()), None)
                if said and spelled is None:
                    raise ValueError(
                        f"{self.name}: the stock's {what} {said!r} isn't one of its {what}s"
                    )
                setattr(v, what, spelled or "")
        if self.variants:
            self.stock = None
        return self

    def stock_index(self, size: str, colour: str) -> int | None:
        """Which count a line of this size and colour takes from: a variant's index, `OWN_STOCK` for
        the product's own stock, or None when it isn't counted."""
        if not self.variants:
            return None if self.stock is None else OWN_STOCK
        return next((i for i, v in enumerate(self.variants) if v.matches(size, colour)), None)

    def stock_at(self, stock_index: int) -> int:
        """How many are left on that count."""
        return (self.stock or 0) if stock_index == OWN_STOCK else self.variants[stock_index].stock

    def display_name(self, stock_index: int) -> str:
        """ "Cotton Panjabi", or with a variant "Cotton Panjabi (L/White)"."""
        return (
            self.name
            if stock_index == OWN_STOCK
            else f"{self.name} ({self.variants[stock_index].label()})"
        )

    def available_variants(self) -> str:
        """The variants that aren't sold out, for a hint: "M, XL/White"."""
        return ", ".join(v.label() for v in self.variants if v.stock)

    def sold_out(self) -> bool:
        """Every size and colour is counted and at 0."""
        every = [(s, c) for s in self.sizes or [""] for c in self.colours or [""]]
        stock_indexes = [self.stock_index(s, c) for s, c in every]
        return all(n is not None and self.stock_at(n) == 0 for n in stock_indexes)

    def in_stock(self, what: str, size: str = "", colour: str = "") -> list[str]:
        """The sizes (or colours) that aren't sold out, with the colour (or size) already
        chosen: the buttons a buyer taps."""

        def left(s: str, c: str) -> bool:
            stock_index = self.stock_index(s, c)
            return stock_index is None or self.stock_at(stock_index) > 0

        colours = [colour] if colour else self.colours or [""]
        sizes = [size] if size else self.sizes or [""]
        if what == "size":
            return [s for s in self.sizes if any(left(s, c) for c in colours)]
        return [c for c in self.colours if any(left(s, c) for s in sizes)]

    def prompt_line(self, currency: str) -> str:
        """One product as a prompt line: `- Cotton Panjabi [panjabi] ৳1,850, sizes M/L…`"""
        line = f"- {self.name} [{self.code}] {money(self.price, currency)}"
        if self.was_price and self.was_price > self.price:
            line += f" (was {money(self.was_price, currency)}, on sale)"
        if self.sizes:
            line += f", sizes {'/'.join(self.sizes)}"
        if self.colours:
            line += f", colours {'/'.join(self.colours)}"
        if self.description:
            line += f": {self.description}"
        if self.sold_out():
            return line + " SOLD OUT"
        if self.stock is not None and self.stock <= LOW_STOCK:
            line += f" (only {self.stock} left)"
        gone = [v.label() for v in self.variants if v.stock == 0]
        if gone:
            line += f" (sold out: {', '.join(gone)})"
        line += "".join(
            f" (only {v.stock} left: {v.label()})"
            for v in self.variants
            if 0 < v.stock <= LOW_STOCK
        )
        return line

    def match_option(self, what: str, said: str) -> str:
        """The size or colour `said` names, spelled as the shop spells it; "" when the product
        has none. Raises ValueError with a hint for the model."""
        choices: list[str] = getattr(self, what + "s")
        if not choices:
            return ""
        for choice in choices:
            if choice.lower() == said.strip().lower():
                return choice
        asked = f"{said!r} isn't one of them. " if said.strip() else ""
        raise ValueError(f"{self.name}: {asked}Ask which {what}: {', '.join(choices)}.")


MAX_PHOTO_MATCHES = 3  # products named in a photo's line


def photo_line(products: list[Product], matches: list[tuple[str, float]] | None) -> str:
    """What the model reads about a visitor's photo: the products whose photos are most
    like it, best first, each once. `matches` are (photo URL, score), best first; a URL on no
    product (one the owner removed) is skipped. None: the photo couldn't be searched."""
    if matches is None:
        return "[Photo. It couldn't be checked: ask what it is.]"
    by_url = {url: p for p in products for url in p.photos}
    found = [by_url[url] for url, _ in matches if url in by_url]
    names = list(dict.fromkeys(f"{p.name} [{p.code}]" for p in found))[:MAX_PHOTO_MATCHES]
    if not names:
        return "[Photo. Nothing in the shop looks like it.]"
    return f"[Photo. Closest products in the shop: {', '.join(names)}]"


def groups_text(products: list[Product]) -> str:
    """Each group and how many products it has, in the shop's order: "Panjabi (80), Saree (60)"."""
    counts: dict[str, int] = {}
    for p in products:
        counts[p.category] = counts.get(p.category, 0) + 1
    return ", ".join(f"{group} ({n})" for group, n in counts.items())


def find_products(
    products: list[Product], words: str, group: str = ""
) -> tuple[list[Product], bool]:
    """The products in `group` (any group when it's empty) that have every one of `words` in
    their name, group, description, sizes or colours, in the shop's order, and True. When none
    has them all: the ones with the most (in a group, the whole group if none has any), and
    False. Plain word matching: "lal" doesn't find Red,
    so the model picks the group, which it can tell from any words."""
    if group:
        products = [p for p in products if p.category.lower() == group.lower()]
    wanted = {w for w in re.findall(r"\w+", words.lower()) if len(w) > 1}

    def hits(p: Product) -> int:
        text = " ".join([p.name, p.category, p.description, *p.sizes, *p.colours]).lower()
        return sum(w in text for w in wanted)

    counted = [(hits(p), p) for p in products]
    every = [p for n, p in counted if n == len(wanted)]
    if every:
        return every, True
    best = max((n for n, _ in counted), default=0)
    return [p for n, p in counted if n == best and (n or group)], False


def merge_variant_rows(rows: list[Product]) -> list[Product]:
    """Rows with the same name and price are one product: many shops keep one row per
    size and colour ("T-Shirt, M, Black, 100" and "T-Shirt, L, White, 80"). Its sizes and
    colours are all the rows', and each row's count is a variant. The first row gives
    the code, description and category. A different price stays its own product.

    When every row has a count, a size and colour no row has is 0: the shop doesn't make it,
    so it's never offered (on its own, unlisted would mean not counted)."""
    groups: dict[tuple[str, int], list[Product]] = {}
    for p in rows:
        groups.setdefault((p.name.strip().lower(), p.price), []).append(p)
    return [
        group[0] if len(group) == 1 else merge_rows_into_product(group) for group in groups.values()
    ]


def merge_rows_into_product(group: list[Product]) -> Product:
    variants: list[Variant] = []
    for p in group:
        if p.variants:
            variants += p.variants
        elif p.stock is not None:  # a row of one size and colour: its count is theirs
            size = p.sizes[0] if len(p.sizes) == 1 else ""
            colour = p.colours[0] if len(p.colours) == 1 else ""
            variants.append(Variant(size=size, colour=colour, stock=p.stock))
    sizes = list(dict.fromkeys(s for p in group for s in p.sizes))
    colours = list(dict.fromkeys(c for p in group for c in p.colours))
    if all(p.variants or p.stock is not None for p in group):
        variants += [
            Variant(size=size, colour=colour, stock=0)
            for size in sizes or [""]
            for colour in colours or [""]
            if not any(v.matches(size, colour) for v in variants)
        ]
    photos = list(dict.fromkeys(link for p in group for link in p.photos))[:3]  # every row's
    update = {
        "sizes": sizes,
        "colours": colours,
        "stock": None,
        "variants": variants,
        "photos": photos,
    }
    return group[0].model_copy(update=update)


# A linked Google Sheet: the owner writes what's on the shelf


def stock_snapshot(product: Product) -> list[Any]:
    """A product's count as the sheet says it, to see next time whether the owner changed it."""
    return [product.stock, [v.model_dump() for v in product.variants]]


def subtract_ordered(product: Product, lines: list[dict[str, Any]]) -> Product:
    """`p` less these order lines (its own), never below 0: what's left to sell."""
    stock, variants = product.stock, [v.model_copy() for v in product.variants]
    for line in lines:
        stock_index = product.stock_index(line["size"], line["colour"])
        if stock_index == OWN_STOCK:
            stock = max(0, (stock or 0) - line["quantity"])
        elif stock_index is not None:
            variants[stock_index].stock = max(0, variants[stock_index].stock - line["quantity"])
    return product.model_copy(update={"stock": stock, "variants": variants})


def merge_sheet_products(
    found: list[Product],
    ours: dict[str, Product],
    seen: dict[str, Any],
    held: list[dict[str, Any]],
) -> list[Product]:
    """The sheet's products (`found`), with our count where the sheet's is the same as last
    time (`seen`), and a new count less what's `held` (the lines of orders not shipped yet).
    Photos are the ones `found` has (the store took them already)."""
    kept = []
    for p in found:
        old = ours.get(p.code)
        if old and seen.get(p.code) == stock_snapshot(p):  # the owner didn't touch this count
            p = p.model_copy(update={"stock": old.stock, "variants": old.variants})
        else:
            p = subtract_ordered(p, [line for line in held if line["code"] == p.code])
        kept.append(p)
    return kept


def merge_products(
    ours: list[Product], found: list[Product], gone: set[str] | None = None
) -> list[Product]:
    """Our list with a source's products in it: each found product updates the one it
    is (the same code, or else the only one with the same name) in its place, or joins the
    end. Ours stay, except the `gone` codes: a linked list's products it doesn't have any more.
    A found product with no photos, videos or goes_with keeps the ones it has here (an owner
    adds them by hand)."""
    kept = [p for p in ours if p.code not in (gone or set())]
    free = set(range(len(kept)))  # ours no found product has updated yet
    for p in found:
        same = [i for i in free if kept[i].code == p.code]
        if not same:
            same = [i for i in free if kept[i].name.strip().lower() == p.name.strip().lower()]
        if len(same) != 1:
            kept.append(p)
            continue
        i = same[0]
        empty = [field for field in FIELDS_KEPT_ON_IMPORT if not getattr(p, field)]
        kept[i] = p.model_copy(update={field: getattr(kept[i], field) for field in empty})
        free.discard(i)
    return kept
