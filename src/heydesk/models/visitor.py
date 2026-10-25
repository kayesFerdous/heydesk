from datetime import date, datetime

from sqlalchemy import JSON, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from heydesk.db import Base, new_id, utcnow


class Visitor(Base):
    """Someone talking to a business's desk, and how much they used it today (D51).

    `key` says who it is on its channel: `web:<the browser's token>` for web chat and voice,
    `telegram:<chat id>` on Telegram. It's also who the agent remembers (D124).
    """

    __tablename__ = "visitors"
    __table_args__ = (UniqueConstraint("workspace_id", "key"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    key: Mapped[str] = mapped_column(String(80))
    day: Mapped[date]  # the business's local day that `turns` counts
    turns: Mapped[int] = mapped_column(default=0)  # turns on `day`
    strikes: Mapped[int] = mapped_column(default=0)  # off-topic turns since the last real one
    # Blocked (I2): why is "team" (someone pressed Block), "flooding" or "off_topic"; None: not
    # blocked. Until when; None with a why: until someone unblocks them
    blocked_why: Mapped[str | None] = mapped_column(String(20), default=None)
    blocked_until: Mapped[datetime | None] = mapped_column(default=None)
    blocked_by: Mapped[str | None] = mapped_column(String(320), default=None)  # their email
    told_blocked: Mapped[bool] = mapped_column(default=False)  # the one notice went (I2)
    ai_off: Mapped[bool] = mapped_column(default=False)  # the team answers them by hand (I2)
    # what the agent remembers about them (D124): `{about: note}`, oldest first, only what they
    # said themselves ("colours": "likes dark colours"). Assign a new dict to change it
    notes: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)
