from datetime import datetime

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from heydesk.db import Base, new_id, utcnow


class PostComment(Base):
    """Someone commented on a Page's post and was answered (D141): once per post and person,
    so a second comment gets no second reply."""

    __tablename__ = "post_comments"
    __table_args__ = (UniqueConstraint("workspace_id", "post_id", "commenter"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    post_id: Mapped[str] = mapped_column(String(64))
    commenter: Mapped[str] = mapped_column(String(64))  # their id in the comment's `from`
    comment_id: Mapped[str] = mapped_column(String(64))  # the first one, the one answered
    conversation_id: Mapped[str | None] = mapped_column(
        ForeignKey("conversations.id"), default=None
    )  # the Messenger chat the private reply opened
    nudged: Mapped[bool] = mapped_column(default=False)  # the one more reply went (D142)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
