from datetime import datetime

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from heydesk.db import Base, new_id, utcnow


class Channel(Base):
    """A business's agent on a messaging app (D42): its Telegram bot. One per app.

    `token` lets anyone run the bot, so it never leaves the server: no route returns it.
    """

    __tablename__ = "channels"
    __table_args__ = (UniqueConstraint("agent_id", "kind"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)  # in its URL
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"))
    kind: Mapped[str] = mapped_column(String(20))  # "telegram"
    token: Mapped[str] = mapped_column(String(200))  # from @BotFather
    secret: Mapped[str] = mapped_column(String(64))  # Telegram sends it with every message
    address: Mapped[str] = mapped_column(String(100))  # the bot's username: spice_garden_bot
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
