"""An online shop's products, delivery areas and orders, as its tools, prompt and routes see
them. Pure."""

import re
from dataclasses import dataclass
from typing import Any, Self

from pydantic import BaseModel, Field, model_validator

from heydesk.desks import csv_import
from heydesk.runtime.config import AgentConfig
from heydesk.runtime.speech import money

LOW_STOCK = 3  # at this many or fewer, the prompt says how many are left and the owner hears
OWN = -1  # `Product.counter`: the product's own stock, not a variant's


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
    """Where the shop delivers, and what that costs: "Inside Dhaka", ৳60, "1–2 days", and the
    places in it that buyers name ("Mirpur", "Uttara")."""

    name: str = Field(min_length=1, max_length=100)
    charge: int = Field(ge=0)  # minor units
    days: str = Field(default="", max_length=50)  # how long it usually takes
    places: list[str] = []  # what buyers say instead of the area's name

    def line(self, currency: str) -> str:
        line = f"- {self.name}: {money(self.charge, currency)}"
        line += f", usually {self.days}" if self.days else ""
        return line + (f" ({', '.join(self.places)})" if self.places else "")


def match_zone(zones: list[Zone], said: str) -> Zone:
    """The one delivery area `said` names: by its name ("inside dhaka", "outside"), or by a
    place in it ("Mirpur 10" → Inside Dhaka). Raises ValueError."""
    text = " ".join(said.lower().split())
    for zone in zones:
        if zone.name.lower() == text:
            return zone
    by_name = [z for z in zones if text and text in z.name.lower()]
    if len(by_name) == 1:
        return by_name[0]
    by_place = [z for z in zones if any(p.lower() in text for p in z.places if p.strip())]
    if len(by_place) == 1:
        return by_place[0]
    names = " or ".join(z.name for z in zones)
    raise ValueError(f"Which delivery area: {names}? Ask.")


class ShopConfig(AgentConfig):
    greeting: str = (
        "Hi, welcome to {business_name}! I can tell you about our products and prices, "
        "take your order, or check on one you've placed."
    )
    delivery: list[Zone] = Field(
        default_factory=list
    )  # where the shop delivers, and the charge for each
    free_delivery_over: int = Field(default=0, ge=0)  # minor units; 0: never free
    policies: str = Field(default="", max_length=4000)  # returns, exchanges
    size_guide: str = Field(default="", max_length=4000)  # each size's measurements, in words
    # A delivered order's size or colour can be swapped within this many days (D55); 0: the
    # agent takes no exchange requests
    exchange_days: int = Field(default=0, ge=0, le=60)
    # The buyer pays the delivery charge online first (SSLCommerz), and the rest on delivery.
    # The order is placed only when the payment comes in (D47)
    pay_first: bool = False

    def delivery_charge(self, zone: Zone, subtotal: int) -> int:
        if self.free_delivery_over and subtotal >= self.free_delivery_over:
            return 0
        return zone.charge


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
# The address, area, mobile or note can change until the courier has it. An unpaid order
# isn't placed yet: cancel it and order again
CHANGEABLE = {"new", "confirmed"}


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
    pay_first: bool = False  # saved as unpaid until the delivery charge is paid online (D47)

    @property
    def total(self) -> int:
        return self.subtotal + self.delivery_charge


@dataclass(frozen=True)
class OrderCancel:
    number: int


@dataclass(frozen=True)
class OrderEdit:
    """An order's delivery details after a change the visitor said yes to (D55). Every field
    is the new value, or the old one where nothing changed. Money in minor units."""

    number: int
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

    return csv_import.parse(text, csv_import.needs(PRODUCT_COLUMNS), row, mapping)


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
