from datetime import datetime

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from heydesk.db import Base, Encrypted, new_id, utcnow


class PaymentAccount(Base):
    """A business's own account with a gateway (D49): payments for its orders go to it. One
    SSLCommerz store and one bKash account at most (D109).

    SSLCommerz uses `store_id` and `password`; bKash `app_key`, `app_secret`, `username` and
    `password`. The secrets take payments in the business's name, so they never leave the
    server: no route returns them."""

    __tablename__ = "payment_accounts"
    __table_args__ = (UniqueConstraint("workspace_id", "provider"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"))
    provider: Mapped[str] = mapped_column(String(20), default="sslcommerz")  # or "bkash"
    store_id: Mapped[str] = mapped_column(String(100), default="")
    password: Mapped[str] = mapped_column(Encrypted())  # encrypted in the database (D74)
    app_key: Mapped[str] = mapped_column(String(100), default="")
    app_secret: Mapped[str | None] = mapped_column(Encrypted())  # None for SSLCommerz
    username: Mapped[str] = mapped_column(String(100), default="")
    sandbox: Mapped[bool] = mapped_column(default=True)  # the gateway's test mode: no real money
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
