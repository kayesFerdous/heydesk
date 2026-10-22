from datetime import datetime

from sqlalchemy import CheckConstraint, String
from sqlalchemy.orm import Mapped, mapped_column

from heydesk.db import Base, new_id, utcnow


class Workspace(Base):
    """A business. A suspended one's chat says "unavailable"; its owner can still log in.
    Why it was suspended is for admins only, never shown to the owner (D70). Its `plan` holds
    until `paid_until` (D106, `services/billing.py`)."""

    __tablename__ = "workspaces"
    __table_args__ = (CheckConstraint("status IN ('active', 'suspended')", name="status"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(10), default="active")
    suspended_reason: Mapped[str] = mapped_column(String(300), default="")  # empty while active
    plan: Mapped[str] = mapped_column(String(10), default="trial")  # a key of billing.PLANS
    paid_until: Mapped[datetime]  # the trial's end, or the last paid day
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
