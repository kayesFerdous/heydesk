"""A restaurant's menu, orders and table bookings, as its tools, prompt and routes see them.
Pure."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field, model_validator

from heydesk.desks import csv_import
from heydesk.runtime.config import AgentConfig, Widget
from heydesk.runtime.speech import money


class MenuItem(BaseModel):
    code: str = Field(min_length=1, max_length=50)  # the short id the model uses: "chk-biryani"
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    price: int = Field(ge=0)  # minor units (paisa), so sums are exact
    category: str
    tags: list[str] = []  # "vegetarian", "spicy", "popular", "contains-nuts"
    available: bool = True

    def line(self, currency: str) -> str:
        """One dish as a prompt line: `- Chicken Biryani [chk-biryani] ৳450 (spicy): Basmati…`"""
        line = f"- {self.name} [{self.code}] {money(self.price, currency)}"
        if self.tags:
            line += f" ({', '.join(self.tags)})"
        if self.description:
            line += f": {self.description}"
        if not self.available:
            line += " NOT AVAILABLE"
        return line


def menu_text(menu: list[MenuItem], currency: str) -> str:
    """The menu for the prompt, by category in the restaurant's order."""
    if not menu:
        return "No menu yet."
    lines = ["Each line: name [code] price (tags): description"]
    for category in dict.fromkeys(item.category for item in menu):
        lines.append(f"{category}:")
        lines += [item.line(currency) for item in menu if item.category == category]
    return "\n".join(lines)


def cart_text(cart: dict[str, Any], currency: str) -> str:
    """The cart `update_order` keeps in the conversation state: "2 × Chicken Biryani, …, total ৳1,030"."""
    lines = ", ".join(f"{line['quantity']} × {line['name']}" for line in cart["lines"])
    return f"{lines}, total {money(cart['total'], currency)}"


@dataclass(frozen=True)
class NewOrder:
    """An order ready to save once the visitor said yes. Money in minor units; `pickup_at` is
    local: when it's picked up, or delivered."""

    lines: list[dict[str, Any]]  # {code, name, quantity, price}, as update_order saved them
    food: int  # the dishes' total
    currency: str
    pickup_at: datetime
    name: str
    phone: str
    note: str = ""
    address: str = ""  # empty: pickup
    delivery_charge: int = 0
    pay_first: bool = False  # saved as unpaid until the delivery charge is paid online (D48)

    @property
    def total(self) -> int:
        return self.food + self.delivery_charge


@dataclass(frozen=True)
class OrderEdit:
    """An order's new time, address or note, after a change the visitor said yes to (D65).
    Every field is the new value, or the old one where nothing changed. `pickup_at` is local."""

    number: int
    pickup_at: datetime
    address: str
    note: str


@dataclass(frozen=True)
class OrderCancel:
    number: int


STATUS_WORDS = {  # what track_order says about each status
    "unpaid": "not placed yet: it's waiting for the delivery charge to be paid online",
    "new": "received: the kitchen has it",
    "ready": "ready",
    "out_for_delivery": "on its way to them",
    "collected": "picked up",
    "delivered": "delivered",
    "cancelled": "cancelled",
}
# An order's steps on the tracking card (D110); unpaid and cancelled are off them
PICKUP_STEPS = [("new", "Received"), ("ready", "Ready"), ("collected", "Picked up")]
DELIVERY_STEPS = [
    ("new", "Received"),
    ("ready", "Ready"),
    ("out_for_delivery", "On its way"),
    ("delivered", "Delivered"),
]
CANCELLABLE = {"unpaid", "new"}  # not once it's ready
# Its time, address or note can change until it's ready. An unpaid order isn't placed yet:
# cancel it and order again
CHANGEABLE = {"new"}


@dataclass(frozen=True)
class NewBooking:
    """A table booking ready to save once the visitor said yes. `at` is local."""

    at: datetime
    people: int
    name: str
    phone: str
    note: str = ""


@dataclass(frozen=True)
class BookingCancel:
    number: int


# A booking keeps its seats until it's done, cancelled or a no-show
HOLDS_SEATS = {"new", "confirmed", "seated"}
CANCELLABLE_BOOKING = {"new", "confirmed"}
STEP = timedelta(minutes=30)  # how far apart the other times we offer are
NEARBY = 6  # steps either side of the asked time: up to 3 hours
OFFERED = 3  # other times offered when a slot is full


def party(people: int, most: int) -> int:
    if people > most:
        raise ValueError(
            f"We take tables for up to {most} people. For a bigger group, offer to take a "
            "message for the team."
        )
    return people


def has_room(
    taken: list[tuple[datetime, int]], at: datetime, people: int, seats: int, minutes: int
) -> bool:
    """Whether `people` more fit from `at` for `minutes`, with the bookings `taken` (their
    start and their people). One shared count of seats, not real tables. The room is
    fullest when someone sits down: at `at`, or when a later booking starts before we leave."""
    keep = timedelta(minutes=minutes)
    moments = [at] + [start for start, _ in taken if at < start < at + keep]
    return all(
        sum(n for start, n in taken if start <= moment < start + keep) + people <= seats
        for moment in moments
    )


def free_times(
    taken: list[tuple[datetime, int]],
    at: datetime,
    people: int,
    config: "RestaurantConfig",
    now: datetime,
) -> list[datetime]:
    """The nearest times on the same day (local) with room for `people`, in clock order."""
    near = [at + STEP * n * sign for n in range(1, NEARBY + 1) for sign in (-1, 1)]
    free = [
        t
        for t in near
        if t > now
        and t.date() == at.date()
        and config.hours.problem_with(t) is None
        and has_room(taken, t, people, config.seats, config.table_minutes)
    ]
    return sorted(free[:OFFERED])


MENU_COLUMNS = [
    csv_import.Column(
        name="name",
        about="the dish's name",
        required=True,
        aliases=[
            "dish",
            "dish name",
            "item",
            "item name",
            "food",
            "menu item",
            "khabar",
            "খাবার",
            "নাম",
        ],
    ),
    csv_import.Column(
        name="price",
        about="its price: 350 or ৳350",
        required=True,
        aliases=["rate", "mrp", "dam", "daam", "দাম", "মূল্য"],
    ),
    csv_import.Column(
        name="category",
        about="the part of the menu: Starters, Rice, Drinks",
        aliases=["section", "group", "type", "menu section", "ক্যাটাগরি", "ধরন"],
    ),
    csv_import.Column(
        name="description",
        about="a line about the dish",
        aliases=["details", "about", "desc", "বিবরণ"],
    ),
    csv_import.Column(
        name="tags",
        about='words about it: "spicy; popular; vegetarian"',
        aliases=["tag", "labels", "keywords"],
    ),
    csv_import.Column(
        name="available",
        about="whether it's served now: yes or no",
        aliases=["availability", "in stock", "active"],
    ),
    csv_import.Column(
        name="code",
        about="the restaurant's own short id for it",
        aliases=["sku", "id", "item code", "কোড"],
    ),
]


def menu_from_csv(text: str, mapping: dict[str, str] | None = None) -> list[MenuItem]:
    """Columns: name, price, and optionally category, description, tags ("spicy; popular"),
    available (yes/no), code. `mapping` names them in the file (D53)."""

    def row(cells: dict[str, str]) -> MenuItem:
        return MenuItem(
            code=cells.get("code") or csv_import.slug(cells["name"]),
            name=cells["name"],
            description=cells.get("description", ""),
            price=csv_import.money(cells["price"]),
            category=cells.get("category") or "Menu",
            tags=csv_import.items(cells.get("tags", "")),
            available=csv_import.available(cells.get("available", "")),
        )

    return csv_import.parse(text, csv_import.needs(MENU_COLUMNS), row, mapping)


class RestaurantConfig(AgentConfig):
    widget: Widget = Widget(
        starters=["What's on the menu?", "I'd like to order", "Are you open now?"]
    )
    greeting: str = (
        "Hi, welcome to {business_name}! Hungry? I can help with the menu or take your order."
    )
    policies: str = ""  # allergens disclaimer, payment, parking…
    delivers: bool = False  # off: pickup only
    delivery_charge: int = Field(default=0, ge=0)  # minor units, the same to any address
    # A delivery order's charge is paid online first (SSLCommerz or bKash); the order is placed
    # only when the payment comes in, and the rest is cash on delivery (D48)
    pay_first: bool = False
    takes_bookings: bool = False  # off: no table bookings, and the agent doesn't offer them
    seats: int = Field(default=0, ge=0)  # people we can seat at once
    table_minutes: int = Field(default=90, ge=15, le=480)  # how long a booking keeps its seats
    max_party: int = Field(default=10, ge=1)  # the most people in one booking

    @model_validator(mode="after")
    def _something_to_pay_first(self) -> "RestaurantConfig":
        if self.pay_first and not (self.delivers and self.delivery_charge):
            raise ValueError(
                "To take the delivery charge first, turn on delivery and set its charge."
            )
        return self

    @model_validator(mode="after")
    def _seats_to_book(self) -> "RestaurantConfig":
        if self.takes_bookings and not self.seats:
            raise ValueError("To take table bookings, set how many seats you have.")
        return self
