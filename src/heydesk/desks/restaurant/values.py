"""A restaurant's menu and orders, as its tools, prompt and routes see them. Pure."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator

from heydesk.desks import csv_import
from heydesk.runtime.config import AgentConfig
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


def menu_from_csv(text: str) -> list[MenuItem]:
    """Columns: name, price, and optionally category, description, tags ("spicy; popular"),
    available (yes/no), code."""

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

    return csv_import.parse(text, {"name", "price"}, row)


class RestaurantConfig(AgentConfig):
    greeting: str = (
        "Hi, welcome to {business_name}! Hungry? I can help with the menu or take your order."
    )
    policies: str = ""  # allergens disclaimer, payment, parking…
    delivers: bool = False  # off: pickup only
    delivery_charge: int = Field(default=0, ge=0)  # minor units, the same to any address
    # A delivery order's charge is paid online first (SSLCommerz); the order is placed only
    # when the payment comes in, and the rest is cash on delivery (D48)
    pay_first: bool = False

    @model_validator(mode="after")
    def _something_to_pay_first(self) -> "RestaurantConfig":
        if self.pay_first and not (self.delivers and self.delivery_charge):
            raise ValueError(
                "To take the delivery charge first, turn on delivery and set its charge."
            )
        return self
