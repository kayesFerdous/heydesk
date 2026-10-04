from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from heydesk.db import Base, new_id, utcnow


class User(Base):
    """Someone who logs in (D23). An owner belongs to one business (workspace) and sees
    only it. An admin runs the platform and belongs to none: the one kind of user without
    a workspace, and a CHECK keeps the two together."""

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('admin', 'owner')", name="role"),
        CheckConstraint("(role = 'admin') = (workspace_id IS NULL)", name="admin_has_no_workspace"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str | None] = mapped_column(ForeignKey("workspaces.id"), index=True)
    role: Mapped[str] = mapped_column(String(10), default="owner")
    email: Mapped[str] = mapped_column(String(320), unique=True)  # stored lowercased
    password_hash: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
