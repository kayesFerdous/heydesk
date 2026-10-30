from datetime import datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from heydesk.db import Base, Encrypted, new_id, utcnow


class WooLink(Base):
    """A shop's list kept in its WooCommerce store, and its orders written there. One per
    agent, shops only, and not with a sheet."""

    __tablename__ = "woo_links"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), unique=True)
    url: Mapped[str] = mapped_column(String(500))  # "https://shop.example.com"
    key: Mapped[str] = mapped_column(String(100))  # ck_…
    secret: Mapped[str] = mapped_column(Encrypted())  # cs_…, encrypted in the database
    # each product code's Woo ids, for writing its order lines: {code: {id, variations}}
    ids: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    checked: Mapped[str] = mapped_column(String(64), default="")  # SHA-256 of the last products
    synced_at: Mapped[datetime | None] = mapped_column(default=None)  # the last good sync
    problem: Mapped[str] = mapped_column(default="")  # empty: fine
    # the webhooks we made in the store, {topic: id}, and their signing secret. None:
    # there was no public address, so the store is polled every WOO_SYNC_MINUTES instead
    hooks: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)
    hook_secret: Mapped[str | None] = mapped_column(Encrypted(), default=None)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)  # orders from then are written
