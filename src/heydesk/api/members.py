"""The business's team (D97): the owner adds and removes moderators, and sees what the team
did. Owners only: a moderator gets a 403 here (`deps.owners_only`)."""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, status
from pydantic import BaseModel

from heydesk.api.auth import EMAIL
from heydesk.api.deps import ClientIp, Paged, Svc, User, Workspace
from heydesk.models import User as UserRow

router = APIRouter(prefix="/api/team", tags=["team"])


class InviteIn(BaseModel):
    email: str = EMAIL


class MemberOut(BaseModel):
    id: str
    email: str
    role: Literal["owner", "moderator"]
    status: Literal["active", "invited"]  # invited: hasn't set a password with the code yet
    invited_at: datetime
    joined_at: datetime | None


class EventOut(BaseModel):
    actor_email: str  # kept after the moderator is removed
    action: str  # "PATCH /api/orders/{order_id}"
    target: str  # the ids in the path: "order_id=…"
    at: datetime


def member(user: UserRow) -> MemberOut:
    return MemberOut(
        id=user.id,
        email=user.email,
        role="owner" if user.role == "owner" else "moderator",
        status="invited" if user.email_verified_at is None else "active",
        invited_at=user.created_at,
        joined_at=user.email_verified_at,
    )


@router.get("/members")
async def members(ws: Workspace, services: Svc) -> list[MemberOut]:
    return [member(u) for u in await services.members.members(ws)]


@router.post("/members", status_code=status.HTTP_201_CREATED)
async def invite(
    body: InviteIn, ws: Workspace, principal: User, services: Svc, ip: ClientIp
) -> MemberOut:
    """A moderator, by email: they get a code and join by setting a password with it
    (`POST /api/auth/reset`). A 409 if the email already has an account, or the team is full."""
    await services.rate_limits.send_code(ip, body.email)  # 429: filling someone's inbox
    return member(await services.members.invite(ws, body.email, principal.user_id))


@router.post("/members/{user_id}/invite", status_code=status.HTTP_204_NO_CONTENT)
async def resend_invite(
    user_id: str, ws: Workspace, principal: User, services: Svc, ip: ClientIp
) -> None:
    """The invite again, with a new code. A 409 once they've joined."""
    await services.rate_limits.send_code(ip, user_id)
    await services.members.resend(ws, user_id, principal.user_id)


@router.delete("/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove(user_id: str, ws: Workspace, services: Svc) -> None:
    """Out at once: their login stops working on the next request. An owner can't be removed."""
    await services.members.remove(ws, user_id)


@router.get("/activity")
async def activity(ws: Workspace, services: Svc, paged: Paged) -> list[EventOut]:
    """What the owners and moderators changed, newest first (`limit`, `before`: D46)."""
    events = await services.members.activity(ws, limit=paged.limit, before=paged.before)
    return [
        EventOut(actor_email=e.actor_email, action=e.action, target=e.target, at=e.at)
        for e in events
    ]
