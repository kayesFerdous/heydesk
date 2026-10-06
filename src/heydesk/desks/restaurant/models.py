from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import JSON, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from heydesk.db import Base, new_id
from heydesk.models.taken import Taken


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


class Order(Taken, Base):
    """A pickup order. Prices are copied in, so a later menu edit doesn't change it."""

    __tablename__ = "orders"
    __table_args__ = (UniqueConstraint("workspace_id", "number"),)
    LETTER: ClassVar[str] = "O"
    FLOW: ClassVar[dict[str, set[str]]] = {
        "new": {"ready", "cancelled"},
        "ready": {"collected", "cancelled"},
        "collected": set(),
        "cancelled": set(),
    }

    lines: Mapped[list[Any]] = mapped_column(JSON)  # {code, name, quantity, price}
    total: Mapped[int]  # minor units
    currency: Mapped[str] = mapped_column(String(3))
    pickup_at: Mapped[datetime]  # UTC
    note: Mapped[str] = mapped_column(default="")
