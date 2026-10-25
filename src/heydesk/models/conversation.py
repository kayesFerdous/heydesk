from datetime import datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from heydesk.db import Base, new_id, utcnow


class Conversation(Base):
    """One visitor's chat or call. `messages` is the whole transcript, only ever added to.

    To change a JSON column, assign a new value: SQLAlchemy doesn't see in-place edits.
    """

    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), index=True)
    channel: Mapped[str] = mapped_column(
        String(20)
    )  # web / voice / telegram / messenger / whatsapp
    # who it is: the chat id on a messaging app, so their next message continues it; the
    # browser's token on the web and a call, so a returning visitor is known (D114)
    contact: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
    # their name as the messaging app gives it (Telegram, WhatsApp); None on the web
    name: Mapped[str | None] = mapped_column(String(100), default=None)
    # active, needs_human (the agent handed off), or human (the team is answering: D43)
    status: Mapped[str] = mapped_column(String(20), default="active")
    # the team member who has it (I1), or None: unassigned
    assigned_to: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True, default=None
    )
    # Done (I3): hidden from the list until the visitor writes again
    done_at: Mapped[datetime | None] = mapped_column(default=None)
    # Snoozed (I3): hidden until then; at that time it comes back with an alert to the owner
    snoozed_until: Mapped[datetime | None] = mapped_column(default=None, index=True)
    state: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict
    )  # what a desk keeps between turns: the cart
    messages: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)
