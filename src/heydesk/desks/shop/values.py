"""An online shop's products, delivery areas and orders, as its tools, prompt and routes see
them. Pure."""

import re
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from heydesk.desks import csv_import
from heydesk.runtime.config import AgentConfig
from heydesk.runtime.speech import money


class Product(BaseModel):
    code: str = Field(min_length=1, max_length=50)  # the short id the model uses: "panjabi-white"
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    price: int = Field(ge=0)  # minor units (paisa)
    category: str = "Products"
    sizes: list[str] = []  # "M", "L", "XL": the visitor picks one, when there are any
    colours: list[str] = []
    stock: int | None = Field(default=None, ge=0)  # None: not counted; 0: sold out

    def line(self, currency: str) -> str:
        """One product as a prompt line: `- Cotton Panjabi [panjabi] ৳1,850, sizes M/L…`"""
        line = f"- {self.name} [{self.code}] {money(self.price, currency)}"
        if self.sizes:
            line += f", sizes {'/'.join(self.sizes)}"
        if self.colours:
            line += f", colours {'/'.join(self.colours)}"
        if self.description:
            line += f": {self.description}"
        if self.stock == 0:
            line += " SOLD OUT"
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
    """Where the shop delivers, and what that costs: "Inside Dhaka", ৳60, "1–2 days"."""

    name: str = Field(min_length=1, max_length=100)
    charge: int = Field(ge=0)  # minor units
    days: str = Field(default="", max_length=50)  # how long it usually takes

    def line(self, currency: str) -> str:
        line = f"- {self.name}: {money(self.charge, currency)}"
        return line + (f", usually {self.days}" if self.days else "")


def match_zone(zones: list[Zone], said: str) -> Zone:
    """The one delivery area `said` names ("inside dhaka", "outside"). Raises ValueError."""
    text = " ".join(said.lower().split())
    for zone in zones:
        if zone.name.lower() == text:
            return zone
    found = [z for z in zones if text and text in z.name.lower()]
    if len(found) == 1:
        return found[0]
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


def products_from_csv(text: str) -> list[Product]:
    """Columns: name, price, and optionally category, description, sizes ("M; L; XL"),
    colours, stock (empty: not counted), code."""

    def row(cells: dict[str, str]) -> Product:
        stock = cells.get("stock", "")
        return Product.model_validate(
            {
                "code": cells.get("code") or csv_import.slug(cells["name"]),
                "name": cells["name"],
                "description": cells.get("description", ""),
                "price": csv_import.money(cells["price"]),
                "category": cells.get("category") or "Products",
                "sizes": csv_import.items(cells.get("sizes", "")),
                "colours": csv_import.items(cells.get("colours") or cells.get("colors", "")),
                "stock": stock or None,
            }
        )

    return csv_import.parse(text, {"name", "price"}, row)
