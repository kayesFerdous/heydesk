"""The team's say over one visitor: block or unblock them, or turn the AI off for them."""

from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel

from heydesk.api.deps import LoggedInUser, Svc, WorkspaceId
from heydesk.models import Visitor
from heydesk.services.visitors import is_blocked

router = APIRouter(tags=["visitors"])


class VisitorOut(BaseModel):
    """What the team decided about one visitor."""

    id: str
    blocked: bool
    blocked_why: str | None  # "team", "flooding" or "off_topic"
    blocked_until: datetime | None  # None while blocked: until someone unblocks them
    blocked_by: str | None  # the email of who pressed Block
    ai_off: bool  # the team answers them by hand


def visitor_out(visitor: Visitor, now: datetime) -> VisitorOut:
    blocked = is_blocked(visitor, now)
    return VisitorOut(
        id=visitor.id,
        blocked=blocked,
        blocked_why=visitor.blocked_why if blocked else None,
        blocked_until=visitor.blocked_until if blocked else None,
        blocked_by=visitor.blocked_by if blocked else None,
        ai_off=visitor.ai_off,
    )


class BlockedOut(VisitorOut):
    conversation_id: str | None  # their latest chat
    channel: str | None
    name: str | None


class VisitorIn(BaseModel):
    blocked: bool | None = None  # true: no reply at all, until false
    ai_off: bool | None = None  # true: their messages go to the team, with no AI turn


@router.patch("/api/visitors/{visitor_id}")
async def change_visitor(
    visitor_id: str,
    body: VisitorIn,
    services: Svc,
    workspace_id: WorkspaceId,
    current_user: LoggedInUser,
) -> VisitorOut:
    """Block or unblock a visitor, or turn the AI off or on for them; the owner or a
    moderator. Their messages run no turn and cost nothing either way."""
    emails = {u.id: u.email for u in await services.members.members(workspace_id)}
    by = emails.get(current_user.user_id, "HeyDesk")  # an admin isn't on the team
    visitor = await services.visitors.change(
        workspace_id, visitor_id, by, body.blocked, body.ai_off
    )
    return visitor_out(visitor, services.visitors.now())


@router.get("/api/visitors/blocked")
async def blocked(services: Svc, workspace_id: WorkspaceId) -> list[BlockedOut]:
    """Who is blocked now, by the team, for flooding or for chatting off topic, with their
    latest chat; the newest block first."""
    now = services.visitors.now()
    found = []
    for v in await services.visitors.blocked(workspace_id):
        conversation = await services.inbox.latest_for_visitor(workspace_id, v.key)
        found.append(
            BlockedOut(
                **visitor_out(v, now).model_dump(),
                conversation_id=conversation.id if conversation else None,
                channel=conversation.channel if conversation else None,
                name=conversation.name if conversation else None,
            )
        )
    return found
