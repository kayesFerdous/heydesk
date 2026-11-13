from typing import ClassVar

from sqlalchemy import String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from heydesk.db import Base
from heydesk.models.record import Record


class TeamMessage(Record, Base):
    """A message for the team or a call-back request."""

    __tablename__ = "team_messages"
    __table_args__ = (UniqueConstraint("workspace_id", "number"),)
    CODE_LETTER: ClassVar[str] = "M"
    STATUS_FLOW: ClassVar[dict[str, set[str]]] = {"new": {"handled"}, "handled": {"new"}}

    body: Mapped[str]
    best_time: Mapped[str] = mapped_column(String(100), default="")  # when to call back
