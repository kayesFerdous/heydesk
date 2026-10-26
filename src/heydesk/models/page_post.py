from datetime import datetime

from sqlalchemy import JSON, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from heydesk.db import Base, new_id, utcnow


class PagePost(Base):
    """What a Page's post is about (D141, D142), found once, at its first comment: its words
    and the products they name, or its photo's closest ones. Every later comment uses it
    again, so the post isn't fetched or searched twice. The owner can set the products
    (`tagged`); prices and stock are read from the list each time."""

    __tablename__ = "page_posts"
    __table_args__ = (UniqueConstraint("workspace_id", "post_id"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    post_id: Mapped[str] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(String(2000), default="")  # the post's words
    products: Mapped[list[str]] = mapped_column(JSON, default=list)  # product codes, in order
    tagged: Mapped[bool] = mapped_column(default=False)  # the owner set the products
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
