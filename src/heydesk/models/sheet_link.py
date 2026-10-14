from datetime import datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from heydesk.db import Base, new_id, utcnow


class SheetLink(Base):
    """A business's list kept in a Google Sheet, read every few minutes (D58). One per agent."""

    __tablename__ = "sheet_links"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), unique=True)
    url: Mapped[str] = mapped_column(String(500))  # as the owner pasted it
    mapping: Mapped[dict[str, str]] = mapped_column(JSON)  # our field → their column (D53)
    # each product's count as the sheet last said it, so a count is taken only when it changes
    seen: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    checked: Mapped[str] = mapped_column(String(64), default="")  # SHA-256 of the last text
    synced_at: Mapped[datetime | None] = mapped_column(default=None)  # the last good read
    problem: Mapped[str] = mapped_column(default="")  # empty: fine
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
