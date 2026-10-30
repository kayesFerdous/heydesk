from datetime import datetime

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from heydesk.db import Base, Encrypted, new_id, utcnow


class CourierLink(Base):
    """A shop's courier, which the agent books confirmed orders with. One per agent,
    shops only."""

    __tablename__ = "courier_links"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), unique=True)
    kind: Mapped[str] = mapped_column(String(20))  # "steadfast" or "pathao"
    key: Mapped[str] = mapped_column(String(200))  # Steadfast's API key; Pathao's client id
    secret: Mapped[str] = mapped_column(Encrypted())  # encrypted in the database
    username: Mapped[str] = mapped_column(String(200), default="")  # Pathao's merchant login
    password: Mapped[str] = mapped_column(Encrypted(), default="")
    store_id: Mapped[int | None] = mapped_column(default=None)  # Pathao's pick-up store
    sandbox: Mapped[bool] = mapped_column(default=False)  # Pathao's sandbox
    problem: Mapped[str] = mapped_column(default="")  # empty: fine. Otherwise for the owner
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
