from typing import ClassVar

from sqlalchemy import String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from heydesk.db import Base
from heydesk.models.taken import Taken


class Ticket(Taken, Base):
    """A complaint the agent flagged (D138): open until someone on the team marks it solved.
    One open ticket per chat; who has it is the chat's `assigned_to`."""

    __tablename__ = "tickets"
    __table_args__ = (UniqueConstraint("workspace_id", "number"),)
    LETTER: ClassVar[str] = "C"
    FLOW: ClassVar[dict[str, set[str]]] = {"open": {"solved"}, "solved": {"open"}}

    reason: Mapped[str] = mapped_column(String(300))  # the agent's words for what's wrong
