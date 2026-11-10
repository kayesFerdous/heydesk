"""A shop's orders before and after they're saved: the details read back to a buyer, the
statuses an order moves through, coupons, a buyer's record and the order lines as text. Pure."""

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Self

from pydantic import BaseModel, Field, field_validator, model_validator

from heydesk.runtime.speech import BANGLA_DIGITS, money


class BuyForm(BaseModel):
    """A product page's *Buy now* form: one product, and the buyer's details."""

    size: str = ""
    colour: str = ""
    quantity: int = Field(default=1, ge=1, le=20)
    name: str = ""
    phone: str = ""
    address: str = ""
    district: str = ""
    note: str = ""
    heard: str = ""  # the read-back the buyer was shown, sent back with Place order
    change: str = ""  # "1": the buyer tapped Change on the check page


class Coupon(BaseModel):
    """A code the owner gives out: a percent or an amount off the items, or free
    delivery; until `ends_at` and for `max_uses` orders, if set. Python works out the discount
    (rule 1)."""

    code: str = Field(min_length=2, max_length=30)  # kept in capitals: "EID10"
    kind: Literal["percent", "amount", "free_delivery"]
    value: int = Field(default=0, ge=0)  # a percent (1–100) or minor units; 0 for free delivery
    ends_at: datetime | None = None  # UTC
    max_uses: int | None = Field(default=None, ge=1)
    used: int = 0  # orders placed with it; the owner doesn't set it

    @field_validator("code")
    @classmethod
    def in_capitals(cls, code: str) -> str:
        return "".join(code.split()).upper()

    @model_validator(mode="after")
    def a_value_that_fits(self) -> Self:
        if self.kind == "percent" and not 1 <= self.value <= 100:
            raise ValueError("A percent coupon takes off 1 to 100.")
        if self.kind == "amount" and self.value < 1:
            raise ValueError("An amount coupon takes off at least 1 paisa.")
        return self

    def problem(self, now: datetime) -> str:
        """Why it can't be used now, for the visitor; "" when it can."""
        ends = self.ends_at and (
            self.ends_at if self.ends_at.tzinfo else self.ends_at.replace(tzinfo=UTC)
        )
        if ends and ends <= now:
            return f"The coupon {self.code} has ended."
        if self.max_uses is not None and self.used >= self.max_uses:
            return f"The coupon {self.code} has been used up."
        return ""

    def discount(self, subtotal: int) -> int:
        """What it takes off the items, in minor units (free delivery takes off the charge)."""
        if self.kind == "percent":
            return subtotal * self.value // 100
        if self.kind == "amount":
            return min(self.value, subtotal)
        return 0

    def describe(self, currency: str) -> str:
        """ "EID10: 10% off", "FLAT100: ৳100 off", "SHIPFREE: free delivery"."""
        if self.kind == "percent":
            return f"{self.code}: {self.value}% off"
        if self.kind == "amount":
            return f"{self.code}: {money(self.value, currency)} off"
        return f"{self.code}: free delivery"


class BuyerRecord(BaseModel):
    """A mobile's orders with this shop: shown on each order before it's sent, since
    a buyer who refused parcels before costs the shop two delivery charges again."""

    orders: int = 0
    delivered: int = 0
    returned: int = 0
    cancelled: int = 0


class Buyer(BuyerRecord):
    """One line of the buyers list: a mobile, and what it did with this shop."""

    phone: str
    name: str  # on their latest order
    spent: int  # minor units, on delivered orders
    last_order_at: datetime
    # Where they ordered from (web, messenger…); an order the team typed has none
    channels: list[str]
    conversations: int


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
    """The cart `update_cart` keeps in the state: "2 × Cotton Panjabi (L, White), …, subtotal
    ৳3,700", and its coupon: "coupon EID10 −৳370" or "coupon SHIPFREE: free delivery"."""
    said = f"{lines_text(cart['lines'])}, subtotal {money(cart['subtotal'], currency)}"
    coupon = cart.get("coupon")
    if coupon and coupon["kind"] == "free_delivery":
        said += f", coupon {coupon['code']}: free delivery"
    elif coupon:
        said += f", coupon {coupon['code']} {money(-cart['discount'], currency)}"
    return said


def lines_text(lines: list[dict[str, Any]]) -> str:
    def one(line: dict[str, Any]) -> str:
        picked = ", ".join(p for p in (line["size"], line["colour"]) if p)
        return f"{line['quantity']} × {line['name']}" + (f" ({picked})" if picked else "")

    return ", ".join(one(line) for line in lines)


def spoken_items(lines: list[dict[str, Any]]) -> str:
    """The order's items as said on a call: "a black Cotton Kurti in size M and 2 Caps".
    `lines_text`'s "1 × … (M, Black)" reads well but sounds odd out loud."""

    def one(line: dict[str, Any]) -> str:
        count = "a" if line["quantity"] == 1 else str(line["quantity"])
        colour = f" {line['colour'].lower()}" if line["colour"] else ""
        size = f" in size {line['size']}" if line["size"] else ""
        return f"{count}{colour} {line['name']}{size}"

    said = [one(line) for line in lines]
    return said[0] if len(said) == 1 else f"{', '.join(said[:-1])} and {said[-1]}"


STATUS_WORDS = {  # what track_order says about each status
    "unpaid": "not placed yet: it's waiting for the delivery charge to be paid online",
    "new": "received: the shop will call to confirm it before sending it",
    "on_hold": "on hold: the shop will be in touch about it",
    "confirmed": "confirmed, and being packed",
    "packed": "packed, and waiting for the courier",
    "shipped": "handed to the courier, on its way",
    "delivered": "delivered",
    "returned": "returned to the shop",
    "cancelled": "cancelled",
}
# An order's steps on the tracking card; unpaid, returned and cancelled are off them
STEPS = [
    ("new", "Received"),
    ("confirmed", "Confirmed"),
    ("packed", "Packed"),
    ("shipped", "With the courier"),
    ("delivered", "Delivered"),
]
CANCELLABLE = {"unpaid", "new", "on_hold", "confirmed", "packed"}  # not once the courier has it
# The items, address, area, mobile or note can change until the courier has it. An unpaid
# order isn't placed yet: cancel it and order again
CHANGEABLE = {"new", "on_hold", "confirmed", "packed"}
# Sold but still on the shelf: a count from a linked sheet includes them
STATUSES_HOLDING_STOCK = {"unpaid", "new", "on_hold", "confirmed", "packed"}
FINISHED_STATUSES = {"delivered", "returned", "cancelled"}  # these take money in and a reason


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
    # "unpaid" until its delivery charge is paid online, "new" until the shop's call,
    # "confirmed" when the shop doesn't call
    status: str = "new"
    coupon: str = ""  # its code; counted as used when the order is saved
    discount: int = 0  # what the coupon took off the items

    @property
    def total(self) -> int:
        return self.subtotal - self.discount + self.delivery_charge


@dataclass(frozen=True)
class OrderCancel:
    number: int


@dataclass(frozen=True)
class OrderConfirm:
    """The buyer said yes on the shop's confirmation call."""

    number: int


@dataclass(frozen=True)
class OrderEdit:
    """An order after a change the visitor said yes to: its items or delivery
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
