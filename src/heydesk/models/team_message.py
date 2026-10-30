from typing import ClassVar

from sqlalchemy import String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from heydesk.db import Base
from heydesk.models.customer_request import CustomerRequest


class TeamMessage(CustomerRequest, Base):
    """A message for the team or a call-back request: every desk can take one."""

    __tablename__ = "team_messages"
    __table_args__ = (UniqueConstraint("workspace_id", "number"),)
    LETTER: ClassVar[str] = "M"
    FLOW: ClassVar[dict[str, set[str]]] = {"new": {"handled"}, "handled": {"new"}}

    body: Mapped[str]
    best_time: Mapped[str] = mapped_column(String(100), default="")  # when to call back
