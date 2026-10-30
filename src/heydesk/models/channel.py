from datetime import datetime

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from heydesk.db import Base, Encrypted, new_id, utcnow


class Channel(Base):
    """A business's agent on a messaging app: its Telegram bot, Facebook Page or
    WhatsApp number.
    One per app.

    `token` lets anyone run the bot, so it never leaves the server: no route returns it,
    and it's encrypted in the database.
    """

    __tablename__ = "channels"
    __table_args__ = (UniqueConstraint("agent_id", "kind"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)  # in its URL
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"))
    kind: Mapped[str] = mapped_column(String(20))  # "telegram", "messenger" or "whatsapp"
    token: Mapped[str] = mapped_column(Encrypted())  # from @BotFather; a Page's or system user's
    # Telegram sends it with every message; empty on Messenger and WhatsApp: Meta signs instead
    secret: Mapped[str] = mapped_column(String(64))
    address: Mapped[str] = mapped_column(
        String(100)
    )  # the bot's username; the Page's id; the number's id
    # the owner's own chat with the bot, for alerts and answering visitors
    owner_chat_id: Mapped[str | None] = mapped_column(String(64), default=None)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
