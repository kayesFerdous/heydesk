from datetime import date, datetime

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from heydesk.db import Base, new_id, utcnow


class Visitor(Base):
    """Someone talking to a business's desk, and how much they used it today (D51).

    `key` says who it is on its channel: `web:<the browser's token>` for web chat and voice,
    `telegram:<chat id>` on Telegram.
    """

    __tablename__ = "visitors"
    __table_args__ = (UniqueConstraint("workspace_id", "key"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    key: Mapped[str] = mapped_column(String(80))
    day: Mapped[date]  # the business's local day that `turns` counts
    turns: Mapped[int] = mapped_column(default=0)  # turns on `day`
    strikes: Mapped[int] = mapped_column(default=0)  # off-topic turns since the last real one
    blocked_until: Mapped[datetime | None] = mapped_column(default=None)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)
