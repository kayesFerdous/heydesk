from datetime import datetime

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from heydesk.db import Base, new_id, utcnow


class Document(Base):
    """A document a business added to its agent's knowledge (D34). The full text is kept here,
    the source of truth: the Qdrant index of its chunks can always be rebuilt from it."""

    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    filename: Mapped[str] = mapped_column(String(255), default="")
    kind: Mapped[str] = mapped_column(String(10))  # "text", "markdown" or "pdf"
    text: Mapped[str] = mapped_column(Text)
    chunk_count: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
