from datetime import datetime

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from heydesk.db import Base, new_id, utcnow

QUEUED, RINGING, TALKING, DONE = "queued", "ringing", "talking", "done"
LIVE = (RINGING, TALKING)


class Call(Base):
    """A call to confirm a shop order (D88, D89): the queue is the calls that aren't `done`.

    queued → ringing → talking → done, or ringing → queued again when it wasn't answered and
    tries are left. `outcome` says how it ended: confirmed, cancelled, no_answer, no_decision,
    skipped (the order moved on first) or stopped (the owner cancelled the call)."""

    __tablename__ = "calls"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)  # the ring's id
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"))
    order_id: Mapped[str] = mapped_column(String(32), index=True)
    code: Mapped[str] = mapped_column(String(20))  # the order's: "O-104"
    name: Mapped[str] = mapped_column(String(100))  # the buyer's, for the owner's list
    phone: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default=QUEUED, index=True)
    outcome: Mapped[str] = mapped_column(String(20), default="")
    tries: Mapped[int] = mapped_column(default=0)  # rings so far
    priority: Mapped[int] = mapped_column(default=0)  # 1 after the owner's "Call now"
    next_try_at: Mapped[datetime]
    rang_at: Mapped[datetime | None] = mapped_column(default=None)  # when it last rang
    conversation_id: Mapped[str | None] = mapped_column(String(32), default=None)  # once answered
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)
