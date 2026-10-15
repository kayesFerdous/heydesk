from datetime import datetime

from sqlalchemy import JSON, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from heydesk.db import Base, new_id, utcnow


class Turn(Base):
    """One agent turn's trace, kept so it can be added up (D71): how long it took, its
    tokens and cost, the tools that ran, what went wrong. Never what anyone said: that's in
    the conversation. Insert-only, like the transcript."""

    __tablename__ = "turns"
    __table_args__ = (Index("ix_turns_workspace_at", "workspace_id", "at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"))
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    channel: Mapped[str] = mapped_column(String(20))  # web, voice, telegram, messenger
    llm: Mapped[str] = mapped_column(String(40))  # the provider: gigalogy, groq, fake
    at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    total_ms: Mapped[int]
    first_output_ms: Mapped[int | None]  # None: no words were streamed (a tool's own reply)
    input_tokens: Mapped[int]
    output_tokens: Mapped[int]
    cost_micros: Mapped[int]  # millionths of a dollar; 0 when the provider has no price
    tools: Mapped[list[str]] = mapped_column(JSON)  # their names, in order
    error: Mapped[str] = mapped_column(String(300), default="")  # empty: it went fine
    handed_off: Mapped[bool]
    interrupted: Mapped[bool]  # a caller talked over the agent
