from datetime import datetime

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from heydesk.db import Base, new_id, utcnow


class PaymentAccount(Base):
    """A business's own SSLCommerz store (D49): payments for its orders go to it. One each.

    `password` takes payments in the business's name, so it never leaves the server: no
    route returns it."""

    __tablename__ = "payment_accounts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), unique=True)
    store_id: Mapped[str] = mapped_column(String(100))
    password: Mapped[str] = mapped_column(String(200))
    sandbox: Mapped[bool] = mapped_column(default=True)  # SSLCommerz's test mode: no real money
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
