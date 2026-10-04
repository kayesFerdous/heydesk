from datetime import datetime

from sqlalchemy import ForeignKey, String, event
from sqlalchemy.orm import Mapped, mapped_column

from heydesk.db import Base, new_id, utcnow


class AuditEvent(Base):
    """What an admin did, or opened, in someone's business (D23). Insert-only."""

    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    workspace_id: Mapped[str | None] = mapped_column(ForeignKey("workspaces.id"), index=True)
    action: Mapped[str] = mapped_column(String(120))  # "PATCH /api/orders/{order_id}"
    target: Mapped[str] = mapped_column(String(200), default="")  # the ids in the path
    at: Mapped[datetime] = mapped_column(default=utcnow, index=True)


@event.listens_for(AuditEvent, "before_update")
def _audit_is_append_only(mapper: object, connection: object, target: AuditEvent) -> None:
    raise RuntimeError("audit events are never updated (D23)")
