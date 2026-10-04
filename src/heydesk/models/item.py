from datetime import datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from heydesk.db import Base, new_id, utcnow


class ItemRow(Base):
    """One of an agent's items: a dish, a service, a product (`runtime.items.Item`, D17)."""

    __tablename__ = "items"
    __table_args__ = (UniqueConstraint("agent_id", "code"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), index=True)
    code: Mapped[str] = mapped_column(String(50))  # the short id the model uses: "chk-biryani"
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(default="")
    price: Mapped[int]  # minor units
    category: Mapped[str] = mapped_column(String(100))
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    available: Mapped[bool] = mapped_column(default=True)
    attributes: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict
    )  # the business's own facts
    position: Mapped[int] = mapped_column(default=0)  # the business's order, for the prompt
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
