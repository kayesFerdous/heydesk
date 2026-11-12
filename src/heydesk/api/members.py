"""The business's team: the owner adds and removes moderators, and sees what the team
did. Owners only: a moderator gets a 403 here (`deps.is_owner_only_route`)."""

from datetime import datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Query, status
from pydantic import BaseModel

from heydesk.api.auth import EMAIL
from heydesk.api.deps import ClientIp, LoggedInUser, Paging, Svc, WorkspaceId
from heydesk.models import User

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
    open_chats: int = 0  # waiting for a person, or with them: what a hand-off goes by
    available: bool = True  # new hand-offs may go to them
    telegram_linked: bool = False  # a moderator's alerts reach their own Telegram


class AvailableIn(BaseModel):
    available: bool


class MemberStatsOut(BaseModel):
    """What one member did in the last `days`."""

    id: str
    email: str
    open_chats: int  # now
    replies: int  # to visitors, from the inbox or Telegram
    chats: int  # the chats they replied in
    tickets_solved: int  # they marked solved, and still are
    leads_sold: int  # they marked sold, and still are


class EventOut(BaseModel):
    actor_email: str  # kept after the moderator is removed
    action: str  # "PATCH /api/orders/{order_id}"
    target: str  # the ids in the path: "order_id=…"
    at: datetime


def member(user: User, open_chats: int = 0) -> MemberOut:
    return MemberOut(
        id=user.id,
        email=user.email,
        role="owner" if user.role == "owner" else "moderator",
        status="invited" if user.email_verified_at is None else "active",
        invited_at=user.created_at,
        joined_at=user.email_verified_at,
        open_chats=open_chats,
        available=user.available,
        telegram_linked=user.telegram_chat_id is not None,
    )


@router.get("/members")
async def members(workspace_id: WorkspaceId, services: Svc) -> list[MemberOut]:
    """The team, each with their open chats."""
    open_chats = await services.inbox.open_chats(workspace_id)
    return [
        member(u, open_chats.get(u.id, 0)) for u in await services.members.members(workspace_id)
    ]


@router.post("/members", status_code=status.HTTP_201_CREATED)
async def invite(
    body: InviteIn,
    workspace_id: WorkspaceId,
    current_user: LoggedInUser,
    services: Svc,
    ip: ClientIp,
) -> MemberOut:
    """A moderator, by email: they get a code and join by setting a password with it
    (`POST /api/auth/reset`). A 409 if the email already has an account, or the team is full."""
    await services.rate_limits.send_code(ip, body.email)  # 429: filling someone's inbox
    return member(await services.members.invite(workspace_id, body.email, current_user.user_id))


@router.post("/members/{user_id}/invite", status_code=status.HTTP_204_NO_CONTENT)
async def resend_invite(
    user_id: str, workspace_id: WorkspaceId, current_user: LoggedInUser, services: Svc, ip: ClientIp
) -> None:
    """The invite again, with a new code. A 409 once they've joined."""
    await services.rate_limits.send_code(ip, user_id)
    await services.members.resend(workspace_id, user_id, current_user.user_id)


@router.delete("/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove(user_id: str, workspace_id: WorkspaceId, services: Svc) -> None:
    """Out at once: their login stops working on the next request. An owner can't be removed."""
    await services.members.remove(workspace_id, user_id)


@router.put("/members/{user_id}/available", status_code=status.HTTP_204_NO_CONTENT)
async def set_available(
    user_id: str, body: AvailableIn, workspace_id: WorkspaceId, services: Svc
) -> None:
    """The owner switches anyone on or off for new hand-offs; a moderator switches
    themself at `/api/auth/me/available`."""
    await services.members.set_available(workspace_id, user_id, body.available)


@router.get("/stats")
async def stats(
    workspace_id: WorkspaceId, services: Svc, days: Annotated[int, Query(ge=1, le=90)] = 7
) -> list[MemberStatsOut]:
    """Each member's work in the last `days`, the owners first."""
    since = services.members.now() - timedelta(days=days)
    done = await services.members.stats(workspace_id, since)
    open_chats = await services.inbox.open_chats(workspace_id)
    none = {"replies": 0, "chats": 0, "tickets_solved": 0, "leads_sold": 0}
    return [
        MemberStatsOut(
            id=u.id, email=u.email, open_chats=open_chats.get(u.id, 0), **done.get(u.id, none)
        )
        for u in await services.members.members(workspace_id)
    ]


@router.get("/activity")
async def activity(workspace_id: WorkspaceId, services: Svc, paged: Paging) -> list[EventOut]:
    """What the owners and moderators changed, newest first (`limit`, `before`)."""
    events = await services.members.activity(workspace_id, limit=paged.limit, before=paged.before)
    return [
        EventOut(actor_email=e.actor_email, action=e.action, target=e.target, at=e.at)
        for e in events
    ]
