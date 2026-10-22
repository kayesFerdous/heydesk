from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from heydesk.db import Base, new_id, utcnow


class SubscriptionPayment(Base):
    """A business paying HeyDesk for a plan (D106): made unpaid when the owner starts paying,
    marked paid once our SSLCommerz store says so. Its id is the gateway's `tran_id`."""

    __tablename__ = "subscription_payments"
    __table_args__ = (CheckConstraint("status IN ('unpaid', 'paid')", name="status"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    plan: Mapped[str] = mapped_column(String(10))
    months: Mapped[int]  # 1 or 12
    amount: Mapped[int]  # minor units, BDT
    status: Mapped[str] = mapped_column(String(10), default="unpaid")
    payment_ref: Mapped[str] = mapped_column(String(100), default="")  # the gateway's id
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    paid_at: Mapped[datetime | None]
