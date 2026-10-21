"""An online shop's products, delivery areas and orders, as its tools, prompt and routes see
them. Pure."""

import html
import re
from dataclasses import dataclass
from datetime import time
from typing import Any, Self

from pydantic import BaseModel, Field, field_validator, model_validator

from heydesk.desks import csv_import
from heydesk.desks.shop.districts import district
from heydesk.runtime.config import AgentConfig, Widget
from heydesk.runtime.speech import money

LOW_STOCK = 3  # at this many or fewer, the prompt says how many are left and the owner hears
OWN = -1  # `Product.counter`: the product's own stock, not a variant's
LONG_LIST = 60  # more products than this, and the prompt lists the groups, not each one (D80)
FOUND = 8  # the most products find_product returns


class Variant(BaseModel):
    """How many of one size and colour are left (D56). An empty size or colour means any of
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
    # Stock per size and colour (D56). When there are any, they hold the stock and `stock` is
    # None; a size and colour no variant matches isn't counted
    variants: list[Variant] = []
    # Up to 3 photo URLs, from `POST /api/products/photos`; the first is the one the agent shows
    photos: list[str] = Field(default=[], max_length=3)

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

    def counter(self, size: str, colour: str) -> int | None:
        """Which count a line of this size and colour takes from: a variant's index, `OWN` for
        the product's own stock, or None when it isn't counted."""
        if not self.variants:
            return None if self.stock is None else OWN
        return next((i for i, v in enumerate(self.variants) if v.matches(size, colour)), None)

    def count(self, counter: int) -> int:
        """How many are left on that count."""
        return (self.stock or 0) if counter == OWN else self.variants[counter].stock

    def what(self, counter: int) -> str:
        """ "Cotton Panjabi", or with a variant "Cotton Panjabi (L/White)"."""
        return self.name if counter == OWN else f"{self.name} ({self.variants[counter].label()})"

    def left(self) -> str:
        """The variants that aren't sold out, for a hint: "M, XL/White"."""
        return ", ".join(v.label() for v in self.variants if v.stock)

    def sold_out(self) -> bool:
        """Every size and colour is counted and at 0."""
        every = [(s, c) for s in self.sizes or [""] for c in self.colours or [""]]
        counters = [self.counter(s, c) for s, c in every]
        return all(n is not None and self.count(n) == 0 for n in counters)

    def line(self, currency: str) -> str:
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

    def choose(self, what: str, said: str) -> str:
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


class Zone(BaseModel):
    """Where the shop delivers, and what that costs: "Inside Dhaka", ৳60, "1–2 days". It covers
    its `districts`, and its `places`: thanas or areas that go here rather than with their
    district ("Savar" in Dhaka suburbs, though Savar is in Dhaka). One with neither covers
    everywhere else (D98)."""

    name: str = Field(min_length=1, max_length=100)
    charge: int = Field(ge=0)  # minor units
    days: str = Field(default="", max_length=50)  # how long it usually takes
    districts: list[str] = []  # of Bangladesh's 64, spelled as in `DISTRICTS`
    places: list[str] = []
    # The buyer pays this area's charge online first, and that confirms the order (D92): BD
    # shops ask it outside Dhaka, where a refused parcel costs the most
    pay_first: bool = False

    @field_validator("districts")
    @classmethod
    def known_districts(cls, names: list[str]) -> list[str]:
        return [district(name) for name in names if name.strip()]

    @property
    def everywhere_else(self) -> bool:
        return not self.districts and not any(p.strip() for p in self.places)

    def line(self, currency: str) -> str:
        line = f"- {self.name}: {money(self.charge, currency)}"
        line += f", usually {self.days}" if self.days else ""
        return line + f" ({', '.join(self.districts + self.places) or 'everywhere else'})"


def _words(text: str) -> str:
    return " " + " ".join(re.sub(r"[,.;:/()\-]", " ", text.lower()).split()) + " "


def match_zone(zones: list[Zone], said_district: str, address: str = "") -> Zone:
    """The delivery area of an address in `said_district` (D98): the one naming a place in the
    address ("Savar" → Dhaka suburbs), else the one with its district, else the one for
    everywhere else. Raises ValueError."""
    name = district(said_district)
    # ponytail: a place matches anywhere in the address ("Nawabganj" is in "Chapai Nawabganj");
    # tie places to a district if owners hit that
    words = _words(address)
    for covers in (
        lambda z: any(_words(p) in words for p in z.places if p.strip()),
        lambda z: name in z.districts,
        lambda z: z.everywhere_else,
    ):
        found = next((z for z in zones if covers(z)), None)
        if found:
            return found
    raise ValueError(f"The shop doesn't deliver to {name}. Say so, kindly.")


class CallSettings(BaseModel):
    """The shop's calls to confirm new orders (D88, D89): off until the owner turns them on."""

    enabled: bool = False
    wait_minutes: int = Field(default=1, ge=0, le=240)  # after the order, before the call
    start: time = time(10)  # calls only between these, in the business's time
    end: time = time(20)
    tries: int = Field(default=2, ge=1, le=5)  # rings before it gives up
    retry_minutes: int = Field(default=30, ge=1, le=1440)  # between them
    # The call's first line; Python fills in {code} and {total}. The buyer says yes to it.
    opening: str = Field(
        default=(
            "Hello, this is {business_name} calling about your order {code}, total {total}, "
            "paid in cash on delivery. Shall we confirm it and send it out?"
        ),
        min_length=1,
        max_length=500,
    )

    @model_validator(mode="after")
    def hours_in_order(self) -> Self:
        if self.end <= self.start:
            raise ValueError("The calling hours must end after they start.")
        return self


class ShopConfig(AgentConfig):
    widget: Widget = Widget(
        starters=["What's new?", "Track my order", "What's the delivery charge?"]
    )
    greeting: str = "Assalamu alaikum! Welcome to {business_name}. How can we help you?"
    # A cash-on-delivery order waits for the shop's call before it's sent (by hand, or
    # `calls`); off: it's confirmed when placed (D92)
    call_to_confirm: bool = True
    calls: CallSettings = CallSettings()  # calls to confirm new orders (D88, D89)
    delivery: list[Zone] = Field(
        default_factory=list
    )  # where the shop delivers, and the charge for each
    free_delivery_over: int = Field(default=0, ge=0)  # minor units; 0: never free
    policies: str = Field(default="", max_length=4000)  # returns, exchanges
    size_guide: str = Field(default="", max_length=4000)  # each size's measurements, in words
    # A delivered order's size or colour can be swapped within this many days (D55); 0: the
    # agent takes no exchange requests
    exchange_days: int = Field(default=0, ge=0, le=60)

    @property
    def pay_first(self) -> bool:
        """Some area's charge is paid online first (D47, D92): `Store.pays_first` reads it."""
        return any(zone.pay_first for zone in self.delivery)

    @model_validator(mode="after")
    def calls_need_call_to_confirm(self) -> Self:
        if self.calls.enabled and not self.call_to_confirm:
            raise ValueError("Calls confirm orders: turn on call_to_confirm to use them.")
        return self

    @model_validator(mode="after")
    def one_area_for_everywhere_else(self) -> Self:
        if sum(zone.everywhere_else for zone in self.delivery) > 1:
            raise ValueError(
                "Only one delivery area can be for everywhere else: give the others their "
                "districts or places."
            )
        return self

    def delivery_charge(self, zone: Zone, subtotal: int) -> int:
        if self.free_delivery_over and subtotal >= self.free_delivery_over:
            return 0
        return zone.charge


PHOTO_MATCHES = 3  # products named in a photo's line


def photo_line(products: list[Product], matches: list[tuple[str, float]] | None) -> str:
    """What the model reads about a visitor's photo (D57): the products whose photos are most
    like it, best first, each once. `matches` are (photo URL, score), best first; a URL on no
    product (one the owner removed) is skipped. None: the photo couldn't be searched."""
    if matches is None:
        return "[Photo. It couldn't be checked: ask what it is.]"
    by_url = {url: p for p in products for url in p.photos}
    found = [by_url[url] for url, _ in matches if url in by_url]
    names = list(dict.fromkeys(f"{p.name} [{p.code}]" for p in found))[:PHOTO_MATCHES]
    if not names:
        return "[Photo. Nothing in the shop looks like it.]"
    return f"[Photo. Closest products in the shop: {', '.join(names)}]"


def groups_text(products: list[Product]) -> str:
    """Each group and how many products it has, in the shop's order: "Panjabi (80), Saree (60)"."""
    counts: dict[str, int] = {}
    for p in products:
        counts[p.category] = counts.get(p.category, 0) + 1
    return ", ".join(f"{group} ({n})" for group, n in counts.items())


def find_products(products: list[Product], query: str) -> list[Product]:
    """The products whose words match `query` best, at most `FOUND` (D80). A word in the name
    or group counts twice, one in the sizes, colours or description once; a tie keeps the
    shop's order. Plain word matching: "lal" doesn't find Red."""
    words = {w for w in re.findall(r"\w+", query.lower()) if len(w) > 1}

    def score(p: Product) -> int:
        named = f"{p.name} {p.category}".lower()
        rest = " ".join([p.description, *p.sizes, *p.colours]).lower()
        return sum(2 if w in named else 1 if w in rest else 0 for w in words)

    scored = [(score(p), i, p) for i, p in enumerate(products)]
    best = sorted((s for s in scored if s[0]), key=lambda s: (-s[0], s[1]))
    return [p for _, _, p in best[:FOUND]]


BANGLA_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")


def mobile(said: str) -> str:
    """A Bangladeshi mobile number as 11 digits: "+880 1712-345678" or "০১৭১২৩৪৫৬৭৮" →
    "01712345678". A wrong number means a parcel that can't be delivered, so it's strict."""
    digits = re.sub(r"[\s\-().]", "", said.translate(BANGLA_DIGITS)).removeprefix("+")
    if digits.startswith("880"):
        digits = digits[2:]  # 8801712… → 01712…
    if not re.fullmatch(r"01[3-9]\d{8}", digits):
        raise ValueError(
            "A Bangladeshi mobile number has 11 digits and starts with 01, like 01712345678. "
            "Ask for it again."
        )
    return digits


def cart_text(cart: dict[str, Any], currency: str) -> str:
    """The cart `update_cart` keeps in the state: "2 × Cotton Panjabi (L, White), …, subtotal ৳3,700"."""
    return f"{lines_text(cart['lines'])}, subtotal {money(cart['subtotal'], currency)}"


def lines_text(lines: list[dict[str, Any]]) -> str:
    def one(line: dict[str, Any]) -> str:
        picked = ", ".join(p for p in (line["size"], line["colour"]) if p)
        return f"{line['quantity']} × {line['name']}" + (f" ({picked})" if picked else "")

    return ", ".join(one(line) for line in lines)


STATUS_WORDS = {  # what track_order says about each status
    "unpaid": "not placed yet: it's waiting for the delivery charge to be paid online",
    "new": "received: the shop will call to confirm it before sending it",
    "confirmed": "confirmed, and being packed",
    "shipped": "handed to the courier, on its way",
    "delivered": "delivered",
    "returned": "returned to the shop",
    "cancelled": "cancelled",
}
CANCELLABLE = {"unpaid", "new", "confirmed"}  # not once the courier has it
# The items, address, area, mobile or note can change until the courier has it. An unpaid
# order isn't placed yet: cancel it and order again
CHANGEABLE = {"new", "confirmed"}
# Sold but still on the shelf: a count from a linked sheet includes them (D58)
HOLDS = {"unpaid", "new", "confirmed"}


@dataclass(frozen=True)
class NewOrder:
    """An order ready to save once the visitor said yes. Money in minor units."""

    lines: list[dict[str, Any]]  # {code, name, size, colour, quantity, price}, from update_cart
    subtotal: int
    delivery_charge: int
    currency: str
    zone: str
    address: str
    name: str
    phone: str
    note: str = ""
    # "unpaid" until its delivery charge is paid online (D47), "new" until the shop's call,
    # "confirmed" when the shop doesn't call (D92)
    status: str = "new"

    @property
    def total(self) -> int:
        return self.subtotal + self.delivery_charge


@dataclass(frozen=True)
class OrderCancel:
    number: int


@dataclass(frozen=True)
class OrderConfirm:
    """The buyer said yes on the shop's confirmation call (D88)."""

    number: int


@dataclass(frozen=True)
class OrderEdit:
    """An order after a change the visitor said yes to (D55): its items (D92) or delivery
    details. Every field is the new value, or the old one where nothing changed. Money in
    minor units."""

    number: int
    lines: list[dict[str, Any]]  # {code, name, size, colour, quantity, price}
    subtotal: int
    address: str
    zone: str
    delivery_charge: int
    phone: str
    note: str


PRODUCT_COLUMNS = [
    csv_import.Column(name="name", about="the product's name", required=True),
    csv_import.Column(name="price", about="its price: 1850 or ৳1,850", required=True),
    csv_import.Column(name="was_price", about="its price before a sale; empty when not on sale"),
    csv_import.Column(name="category", about="a group it's listed under: Panjabi, Saree"),
    csv_import.Column(name="description", about="a line or two about it"),
    csv_import.Column(name="sizes", about='the sizes it comes in: "M; L; XL"'),
    csv_import.Column(name="colours", about='the colours it comes in: "white; black"'),
    csv_import.Column(
        name="stock",
        about='how many are left, or per size and colour: "M: 5; L: 0"; empty when not counted',
    ),
    csv_import.Column(name="code", about="the shop's own short id or SKU for it"),
]


def products_from_csv(text: str, mapping: dict[str, str] | None = None) -> list[Product]:
    """Columns: name, price, and optionally was_price (before a sale), category, description,
    sizes ("M; L; XL"), colours, stock (a number, "M: 5; L White: 0" per size and colour, or
    empty: not counted), code. `mapping` names them in the file (D53)."""

    def row(cells: dict[str, str]) -> Product:
        sizes = csv_import.items(cells.get("sizes", ""))
        colours = csv_import.items(cells.get("colours") or cells.get("colors", ""))
        stock, variants = stock_cell(cells.get("stock", ""), sizes, colours)
        was = cells.get("was_price", "")
        return Product.model_validate(
            {
                "code": cells.get("code") or csv_import.slug(cells["name"]),
                "name": cells["name"],
                "description": cells.get("description", ""),
                "price": csv_import.money(cells["price"]),
                "was_price": csv_import.money(was) if was else None,
                "category": cells.get("category") or "Products",
                "sizes": sizes,
                "colours": colours,
                "stock": stock,
                "variants": variants,
            }
        )

    return grouped(csv_import.parse(text, csv_import.needs(PRODUCT_COLUMNS), row, mapping))


def grouped(rows: list[Product]) -> list[Product]:
    """Rows with the same name and price are one product (D59): many shops keep one row per
    size and colour ("T-Shirt, M, Black, 100" and "T-Shirt, L, White, 80"). Its sizes and
    colours are all the rows', and each row's count is a variant (D56). The first row gives
    the code, description and category. A different price stays its own product.

    When every row has a count, a size and colour no row has is 0: the shop doesn't make it,
    so it's never offered (on its own, unlisted would mean not counted, D56)."""
    groups: dict[tuple[str, int], list[Product]] = {}
    for p in rows:
        groups.setdefault((p.name.strip().lower(), p.price), []).append(p)
    return [group[0] if len(group) == 1 else one_product(group) for group in groups.values()]


def one_product(group: list[Product]) -> Product:
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
    update = {"sizes": sizes, "colours": colours, "stock": None, "variants": variants}
    return group[0].model_copy(update=update)


def stock_cell(text: str, sizes: list[str], colours: list[str]) -> tuple[int | None, list[Variant]]:
    """A CSV stock cell: "" (not counted), "12" (the product's own stock), or a count per
    size and colour, "M: 5; L: 0" or "L White: 2; L/Navy: 0" (D56). Raises ValueError."""
    text = text.strip()
    if not text:
        return None, []
    if text.isdigit():
        return int(text), []
    names = {  # "l white" → ("L", "White"); "l" → ("L", ""); "white" → ("", "White")
        " ".join(part for part in (size, colour) if part).lower(): (size, colour)
        for size in [*sizes, ""]
        for colour in [*colours, ""]
        if size or colour
    }
    variants = []
    for part in re.split(r"[;\n]", text):
        if not part.strip():
            continue
        said, _, count = part.rpartition(":")
        key = " ".join(said.replace("/", " ").lower().split())
        if key not in names or not count.strip().isdigit():
            raise ValueError(
                f"stock {part.strip()!r}: write a size or colour and a number, like M: 5"
            )
        size, colour = names[key]
        variants.append(Variant(size=size, colour=colour, stock=int(count)))
    return None, variants


# A linked Google Sheet (D58): the owner writes what's on the shelf


def counted(p: Product) -> list[Any]:
    """A product's count as the sheet says it, to see next time whether the owner changed it."""
    return [p.stock, [v.model_dump() for v in p.variants]]


def take_off(p: Product, lines: list[dict[str, Any]]) -> Product:
    """`p` less these order lines (its own), never below 0: what's left to sell."""
    stock, variants = p.stock, [v.model_copy() for v in p.variants]
    for line in lines:
        counter = p.counter(line["size"], line["colour"])
        if counter == OWN:
            stock = max(0, (stock or 0) - line["quantity"])
        elif counter is not None:
            variants[counter].stock = max(0, variants[counter].stock - line["quantity"])
    return p.model_copy(update={"stock": stock, "variants": variants})


def merge(
    found: list[Product],
    ours: dict[str, Product],
    seen: dict[str, Any],
    held: list[dict[str, Any]],
) -> list[Product]:
    """The sheet's products (`found`), with our count where the sheet's is the same as last
    time (`seen`), and a new count less what's `held` (the lines of orders not shipped yet).
    Photos are ours: a sheet has none."""
    kept = []
    for p in found:
        old = ours.get(p.code)
        if old and seen.get(p.code) == counted(p):  # the owner didn't touch this count
            p = p.model_copy(update={"stock": old.stock, "variants": old.variants})
        else:
            p = take_off(p, [line for line in held if line["code"] == p.code])
        kept.append(p.model_copy(update={"photos": old.photos if old else []}))
    return kept


# A linked WooCommerce store (D75): its fields are the same in every store, so they're matched here

SIZE, COLOUR = {"size"}, {"colour", "color"}  # attribute names, in any case
UNSORTED = "Uncategorized"  # Woo's own default category: ours is "Products"


def products_from_woo(items: list[dict[str, Any]]) -> tuple[list[Product], dict[str, Any]]:
    """Woo's published products as ours, and each code's Woo ids for writing its order lines:
    `{code: {"id": 11, "variations": {"M|White": 15}}}`. A variable product's variations are
    its variants (D56); variations that cost differently are one product per price, as rows
    are grouped (D59). What can't be bought (no price, a grouped or external product) is left
    out. Raises ValueError for a product that doesn't fit ours."""
    products: list[Product] = []
    ids: dict[str, Any] = {}
    for item in items:
        if not item.get("price") or item.get("type") not in ("simple", "variable"):
            continue
        code = (item.get("sku") or f"woo-{item['id']}")[:50]
        base = {
            "code": code,
            "name": plain_text(item["name"])[:200],
            "description": woo_about(item),
            "category": woo_category(item),
            "sizes": woo_options(item["attributes"], SIZE),
            "colours": woo_options(item["attributes"], COLOUR),
        }
        if item["type"] == "simple":
            products.append(
                Product.model_validate(base | woo_prices(item) | {"stock": woo_stock(item)})
            )
            ids[code] = {"id": item["id"], "variations": {}}
            continue
        found, by_price = woo_variations(base, item)
        cheapest = min(by_price)
        for price, product in sorted(by_price.items()):
            if price != cheapest:  # another price is another product: "Linen Panjabi (XXL)"
                named = ", ".join(product.sizes or product.colours)
                product = product.model_copy(
                    update={
                        "code": f"{code}-{price // 100}"[:50],
                        "name": f"{product.name} ({named})",
                    }
                )
            products.append(product)
            ids[product.code] = {"id": item["id"], "variations": found[price]}
    return products, ids


def woo_variations(
    base: dict[str, Any], item: dict[str, Any]
) -> tuple[dict[int, dict[str, int]], dict[int, Product]]:
    """A variable product's variations: a row each, grouped by price (D59). Returns, per
    price, the variation ids by "size|colour" and the product."""
    rows: list[Product] = []
    found: dict[int, dict[str, int]] = {}
    for v in item.get("variations", []):
        if not v.get("price"):
            continue
        size = woo_option(v["attributes"], SIZE)
        colour = woo_option(v["attributes"], COLOUR)
        row = base | woo_prices(v) | {"stock": woo_stock(v)}
        row |= {"sizes": [size] if size else base["sizes"]}
        row |= {"colours": [colour] if colour else base["colours"]}
        rows.append(Product.model_validate(row))
        found.setdefault(rows[-1].price, {})[f"{size}|{colour}"] = v["id"]
    by_price = {p.price: p for p in grouped(rows)}
    if not by_price:
        raise ValueError(f"{base['name']}: none of its variations has a price")
    return found, by_price


def woo_ids(ids: dict[str, Any], line: dict[str, Any]) -> tuple[int, int | None]:
    """An order line's Woo product and variation ids. A variation for any size or colour is
    written "|White" or "M|". Raises KeyError when the product isn't in the store any more."""
    product = ids[line["code"]]
    size, colour = line["size"], line["colour"]
    keys = [f"{size}|{colour}", f"|{colour}", f"{size}|", "|"]
    variation = next((product["variations"][k] for k in keys if k in product["variations"]), None)
    if product["variations"] and variation is None:
        raise KeyError(line["code"])
    return product["id"], variation


def woo_prices(item: dict[str, Any]) -> dict[str, int | None]:
    price = csv_import.money(item["price"])
    regular = csv_import.money(item["regular_price"]) if item.get("regular_price") else 0
    return {"price": price, "was_price": regular if regular > price else None}


def woo_stock(item: dict[str, Any]) -> int | None:
    """Woo's count, never below 0 (Woo lets it go under on backorder); a count it doesn't keep
    is 0 when it says out of stock, otherwise not counted."""
    if item.get("manage_stock") is True and item.get("stock_quantity") is not None:
        return max(0, int(item["stock_quantity"]))
    return 0 if item.get("stock_status") == "outofstock" else None


def woo_options(attributes: list[dict[str, Any]], names: set[str]) -> list[str]:
    """A product's sizes or colours: the options of its attribute with one of these names."""
    found = next((a for a in attributes if a["name"].strip().lower() in names), None)
    return [plain_text(o) for o in found["options"]] if found else []


def woo_option(attributes: list[dict[str, Any]], names: set[str]) -> str:
    """A variation's size or colour; "" when it's any of them."""
    found = next((a for a in attributes if a["name"].strip().lower() in names), None)
    return plain_text(found["option"]) if found else ""


def woo_about(item: dict[str, Any]) -> str:
    """Its short description, else its description, as plain text, and its other attributes
    as a line: "Fabric: Linen"."""
    said = plain_text(item.get("short_description") or item.get("description") or "")
    other = [
        f"{plain_text(a['name'])}: {', '.join(plain_text(o) for o in a['options'])}"
        for a in item.get("attributes", [])
        if a["name"].strip().lower() not in SIZE | COLOUR and a.get("options")
    ]
    return " ".join(p if p.endswith(".") else p + "." for p in [said, *other] if p)[:500]


def woo_category(item: dict[str, Any]) -> str:
    names = [plain_text(c["name"]) for c in item.get("categories", []) if c["name"] != UNSORTED]
    return names[0][:100] if names else "Products"


def plain_text(html_text: str) -> str:
    """Woo's HTML as one line of plain text: "<p>Soft <b>linen</b>.</p>" → "Soft linen."."""
    words = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", html_text)).split())
    return re.sub(r" ([.,;:!?])", r"\1", words)  # a tag ended right before it
