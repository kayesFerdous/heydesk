from datetime import datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from heydesk.db import Base, new_id, utcnow


class Order(Base):
    """An order a visitor placed. Saved only through `OrderService.place` (rule 2)."""

    __tablename__ = "orders"
    __table_args__ = (UniqueConstraint("agent_id", "number"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), index=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    number: Mapped[int]  # what the visitor is told: 101, 102… per agent
    lines: Mapped[list[dict[str, Any]]] = mapped_column(JSON)  # {code, name, quantity, price}
    total: Mapped[int]  # minor units
    currency: Mapped[str] = mapped_column(String(3))
    pickup_time: Mapped[datetime]
    customer_name: Mapped[str] = mapped_column(String(100))
    customer_phone: Mapped[str] = mapped_column(String(20))
    note: Mapped[str] = mapped_column(default="")
    status: Mapped[str] = mapped_column(String(20), default="new")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
