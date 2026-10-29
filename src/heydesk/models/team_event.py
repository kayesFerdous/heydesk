from datetime import datetime

from sqlalchemy import ForeignKey, String, event
from sqlalchemy.orm import Mapped, mapped_column

from heydesk.db import Base, new_id, utcnow


class TeamEvent(Base):
    """What someone in a business changed through the API, for its owner. Insert-only.

    No foreign key to `users`: a removed moderator's row is deleted, and what they did stays,
    with the email they had."""

    __tablename__ = "team_events"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    actor_id: Mapped[str] = mapped_column(String(32))
    actor_email: Mapped[str] = mapped_column(String(320))
    action: Mapped[str] = mapped_column(String(120))  # "PATCH /api/orders/{order_id}"
    target: Mapped[str] = mapped_column(String(200), default="")  # the ids in the path
    at: Mapped[datetime] = mapped_column(default=utcnow, index=True)


@event.listens_for(TeamEvent, "before_update")
def _team_events_are_append_only(mapper: object, connection: object, target: TeamEvent) -> None:
    raise RuntimeError("team events are never updated")
