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


class Order(Taken, Payable, Base):
    """A pickup or delivery order. Prices are copied in, so a later menu edit doesn't change it.

    When the restaurant takes the delivery charge first, a delivery order starts `unpaid` and
    only the payment check makes it `new` (D48)."""

    __tablename__ = "orders"
    __table_args__ = (UniqueConstraint("workspace_id", "number"),)
    LETTER: ClassVar[str] = "O"
    PAY_FOR: ClassVar[str] = "the delivery charge"
    FLOW: ClassVar[dict[str, set[str]]] = {
        "unpaid": {"new", "cancelled"},  # paid online; or the owner took it another way
        "new": {"ready", "cancelled"},
        "ready": {"collected", "cancelled"},
        "collected": set(),
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

    def about(self) -> str:
        dishes = ", ".join(f"{line['quantity']} × {line['name']}" for line in self.lines)
        return f"{dishes}; delivery to {self.address}" if self.address else dishes

    def after(self) -> str:
        cod = money(self.total - self.paid, self.currency)
        return f"Your order #{self.number} is placed. The rest, {cod}, is paid on delivery."
