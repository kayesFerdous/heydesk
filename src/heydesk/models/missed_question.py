from datetime import datetime

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from heydesk.db import Base, new_id, utcnow


class MissedQuestion(Base):
    """A question the agent couldn't answer (D143): its documents had nothing, or it handed
    the chat off for it. Kept until the owner teaches the answer (`taught_at`)."""

    __tablename__ = "missed_questions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    conversation_id: Mapped[str | None] = mapped_column(
        ForeignKey("conversations.id"), default=None
    )
    question: Mapped[str] = mapped_column(String(300))  # the model's few words for it
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    taught_at: Mapped[datetime | None] = mapped_column(default=None)
