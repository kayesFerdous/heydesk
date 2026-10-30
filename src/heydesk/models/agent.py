from datetime import datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from heydesk.db import Base, new_id, utcnow


class Agent(Base):
    """A business's agent: one per workspace. Edits to `config` take effect on the next message."""

    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), unique=True)
    kind: Mapped[str] = mapped_column(String(50))  # its desk: "clinic", "restaurant"
    name: Mapped[str] = mapped_column(String(200))
    public_key: Mapped[str] = mapped_column(String(64), unique=True)  # in the chat URL
    config: Mapped[dict[str, Any]] = mapped_column(JSON)  # its desk's config
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)
