from datetime import datetime
from typing import ClassVar

from sqlalchemy import String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from heydesk.db import Base
from heydesk.models.taken import Taken

OUTCOMES = ("sold", "not_now", "lost")


class Lead(Taken, Base):
    """A visitor who asked to be called back at a time (D139): open until the team taps how
    it went. A lost one says why, and the whys are counted."""

    __tablename__ = "leads"
    __table_args__ = (UniqueConstraint("workspace_id", "number"),)
    LETTER: ClassVar[str] = "L"
    FLOW: ClassVar[dict[str, set[str]]] = {
        "open": set(OUTCOMES),
        **{outcome: {"open"} for outcome in OUTCOMES},
    }

    call_at: Mapped[datetime]  # when to call, UTC; Python read it from their words (rule 1)
    note: Mapped[str] = mapped_column(String(300))  # what they want
    why: Mapped[str] = mapped_column(String(200), default="")  # a lost one's reason
