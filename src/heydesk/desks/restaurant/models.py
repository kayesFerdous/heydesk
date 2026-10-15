from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import JSON, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from heydesk.db import Base, new_id
from heydesk.models.taken import Payable, Taken
from heydesk.runtime.speech import money


class MenuItemRow(Base):
    """One dish on a restaurant's menu (`values.MenuItem`)."""

    __tablename__ = "menu_items"
    __table_args__ = (UniqueConstraint("workspace_id", "code"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    code: Mapped[str] = mapped_column(String(50))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(default="")
    price: Mapped[int]  # minor units
    category: Mapped[str] = mapped_column(String(100))
    tags: Mapped[list[Any]] = mapped_column(JSON, default=list)
    available: Mapped[bool] = mapped_column(default=True)
    position: Mapped[int] = mapped_column(default=0)  # the restaurant's order, for the prompt


PICKUP_ONLY = {"collected"}
DELIVERY_ONLY = {"out_for_delivery", "delivered"}


class Order(Taken, Payable, Base):
    """A pickup or delivery order. Prices are copied in, so a later menu edit doesn't change it.

    When the restaurant takes the delivery charge first, a delivery order starts `unpaid` and
    only the payment check makes it `new` (D48). A pickup is collected; a delivery goes out
    and is delivered (D66)."""

    __tablename__ = "orders"
    __table_args__ = (UniqueConstraint("workspace_id", "number"),)
    LETTER: ClassVar[str] = "O"
    PAY_FOR: ClassVar[str] = "the delivery charge"
    FLOW: ClassVar[dict[str, set[str]]] = {
        "unpaid": {"new", "cancelled"},  # paid online; or the owner took it another way
        "new": {"ready", "cancelled"},
        "ready": {"collected", "out_for_delivery", "cancelled"},
        "out_for_delivery": {"delivered", "cancelled"},
        "collected": set(),
        "delivered": set(),
        "cancelled": set(),
    }

    lines: Mapped[list[Any]] = mapped_column(JSON)  # {code, name, quantity, price}
    total: Mapped[int]  # minor units, with the delivery charge; `total - paid` is still owed
    pickup_at: Mapped[datetime]  # UTC: when it's picked up, or delivered
    note: Mapped[str] = mapped_column(default="")
    address: Mapped[str] = mapped_column(String(300), default="")  # empty: pickup
    delivery_charge: Mapped[int] = mapped_column(default=0)

    @property
    def due(self) -> int:
        return self.delivery_charge

    def move(self, status: str, now: datetime) -> None:
        """As `Taken.move`, and a pickup isn't delivered, nor a delivery collected."""
        if status in (PICKUP_ONLY if self.address else DELIVERY_ONLY):
            kind = "delivery" if self.address else "pickup"
            raise ValueError(f"a {kind} order can't become {status}")
        super().move(status, now)

    def news(self) -> str:
        """What the visitor is told in their chat when the owner moves it here; "" for
        nothing (D66)."""
        if self.status == "ready" and not self.address:
            return f"🍽️ Your order {self.code} is ready to pick up."
        if self.status == "out_for_delivery":
            return f"🛵 Your order {self.code} is on its way."
        if self.status == "cancelled":
            return f"Sorry, the restaurant had to cancel your order {self.code}."
        return ""

    def about(self) -> str:
        dishes = ", ".join(f"{line['quantity']} × {line['name']}" for line in self.lines)
        return f"{dishes}; delivery to {self.address}" if self.address else dishes

    def after(self) -> str:
        cod = money(self.total - self.paid, self.currency)
        return f"Your order {self.code} is placed. The rest, {cod}, is paid on delivery."


class Booking(Taken, Base):
    """A table booking. The restaurant confirms it, and the visitor hears so in the chat."""

    __tablename__ = "bookings"
    __table_args__ = (UniqueConstraint("workspace_id", "number"),)
    LETTER: ClassVar[str] = "T"
    FLOW: ClassVar[dict[str, set[str]]] = {
        "new": {"confirmed", "cancelled"},
        "confirmed": {"seated", "cancelled", "no_show"},
        "seated": {"done"},
        "done": set(),
        "cancelled": set(),
        "no_show": set(),
    }

    at: Mapped[datetime]  # UTC: when they arrive
    people: Mapped[int]
    note: Mapped[str] = mapped_column(default="")  # "birthday", "a high chair"

    def news(self) -> str:
        """What the visitor is told in their chat when the owner moves it here; "" for
        nothing (D66)."""
        if self.status == "confirmed":
            return f"✅ Your table for {self.people} is confirmed ({self.code}). See you then!"
        if self.status == "cancelled":
            return f"Sorry, the restaurant had to cancel your booking {self.code}."
        return ""
