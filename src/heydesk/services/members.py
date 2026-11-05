"""A business's team: the owner adds moderators by email, and sees what everyone did.

A moderator is a user of the business with the role `moderator`. Adding one makes the user
with a password no one knows and emails them a code; they join by setting their own password
with it (`POST /api/auth/reset`), which verifies the email. Until then they're "invited".
Removing one deletes the user, and the database is asked on every request, so they're out at
once (`UserService.still_moderator`).

Every write an owner or moderator makes through the API, once it succeeds, is a `TeamEvent`:
who, the route and the ids in its path. Never anyone's words.
"""

import secrets
from collections.abc import Callable
from datetime import datetime
from typing import Any

from sqlalchemy import distinct, func, literal, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from heydesk.errors import Conflict, NotFound
from heydesk.models import Conversation, Lead, TeamEvent, Ticket, User, Workspace
from heydesk.services.billing import PLANS, count_moderators
from heydesk.services.team import TELEGRAM_REPLY
from heydesk.services.users import CurrentUser, UserService

# a reply to a visitor, from the inbox or Telegram
REPLIES = ("POST /api/conversations/{conversation_id}/reply", TELEGRAM_REPLY)


class MemberService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        users: UserService,
        now: Callable[[], datetime],
    ) -> None:
        self.sessions = sessions
        self.users = users
        self.now = now

    async def members(self, workspace_id: str) -> list[User]:
        """The owners, then the moderators ("owner" sorts after "moderator"), oldest first."""
        async with self.sessions() as s:
            query = select(User).where(User.workspace_id == workspace_id)
            return list(await s.scalars(query.order_by(User.role.desc(), User.created_at)))

    async def invite(self, workspace_id: str, email: str, inviter_id: str) -> User:
        """A new moderator, and an email with the code to join."""
        email = email.strip().lower()
        async with self.sessions() as s, s.begin():
            taken = await s.scalar(select(User).where(User.email == email))
            if taken is not None:
                raise Conflict(
                    "They're already on your team."
                    if taken.workspace_id == workspace_id
                    else "That email already has a HeyDesk account, so it can't join your team."
                )
            moderators = await count_moderators(s, workspace_id)
            business = await s.get(Workspace, workspace_id)
            seats = PLANS[business.plan].moderators if business else 0  # the plan's
            if moderators >= seats:
                raise Conflict(
                    f"Your plan has {seats} moderator{'s' if seats > 1 else ''}. "
                    "Choose a bigger plan on the billing page to add more."
                )
            password = secrets.token_urlsafe(32)  # no one knows it: they set their own to join
            user = await self.users.new_user(
                s, email, password, role="moderator", workspace_id=workspace_id, verified=False
            )
        await self._send_invite(user, inviter_id)
        return user

    async def resend(self, workspace_id: str, user_id: str, inviter_id: str) -> None:
        """The invite again, with a new code; a 409 once they've joined."""
        async with self.sessions() as s:
            user = await self._moderator(s, workspace_id, user_id)
        if user.email_verified_at is not None:
            raise Conflict("They've already joined.")
        await self._send_invite(user, inviter_id)

    async def remove(self, workspace_id: str, user_id: str) -> None:
        """A moderator leaves the team, invited or joined, and their chats go back to
        unassigned. An owner can't be removed."""
        async with self.sessions() as s, s.begin():
            await s.delete(await self._moderator(s, workspace_id, user_id))
            await s.execute(
                update(Conversation)
                .where(
                    Conversation.workspace_id == workspace_id, Conversation.assigned_to == user_id
                )
                .values(assigned_to=None)
            )

    async def set_available(self, workspace_id: str, user_id: str, available: bool) -> None:
        """Whether new hand-offs may go to them."""
        async with self.sessions() as s, s.begin():
            user = await s.scalar(
                select(User).where(User.id == user_id, User.workspace_id == workspace_id)
            )
            if user is None:
                raise NotFound("There's no such member on your team.")
            user.available = available

    async def stats(self, workspace_id: str, since: datetime) -> dict[str, dict[str, int]]:
        """Each member's work since `since`, from the activity: replies to visitors, the
        chats they replied in, and the tickets they marked solved and leads they marked sold
        (still so). `{user_id: {...}}`; a member who did nothing isn't in it."""

        def tapped(model: type[Ticket] | type[Lead], key: str, status: str) -> Any:
            return (
                select(TeamEvent.actor_id, func.count(distinct(model.id)))
                .join(model, TeamEvent.target == literal(f"{key}=") + model.id)
                .where(
                    TeamEvent.workspace_id == workspace_id,
                    TeamEvent.at >= since,
                    TeamEvent.action.startswith("PATCH "),
                    model.workspace_id == workspace_id,
                    model.status == status,
                )
                .group_by(TeamEvent.actor_id)
            )

        replies = (
            select(TeamEvent.actor_id, func.count(), func.count(distinct(TeamEvent.target)))
            .where(
                TeamEvent.workspace_id == workspace_id,
                TeamEvent.at >= since,
                TeamEvent.action.in_(REPLIES),
            )
            .group_by(TeamEvent.actor_id)
        )
        out: dict[str, dict[str, int]] = {}

        def add(user_id: str, **counts: int) -> None:
            out.setdefault(
                user_id, {"replies": 0, "chats": 0, "tickets_solved": 0, "leads_sold": 0}
            ).update(counts)

        async with self.sessions() as s:
            for user_id, n, chats in (await s.execute(replies)).all():
                add(user_id, replies=n, chats=chats)
            for user_id, n in (await s.execute(tapped(Ticket, "ticket_id", "solved"))).all():
                add(user_id, tickets_solved=n)
            for user_id, n in (await s.execute(tapped(Lead, "lead_id", "sold"))).all():
                add(user_id, leads_sold=n)
        return out

    async def record(self, current_user: CurrentUser, action: str, target: str) -> None:
        assert current_user.workspace_id is not None
        async with self.sessions() as s, s.begin():
            email = await s.scalar(select(User.email).where(User.id == current_user.user_id))
            s.add(
                TeamEvent(
                    workspace_id=current_user.workspace_id,
                    actor_id=current_user.user_id,
                    actor_email=email or "",
                    action=action[:120],
                    target=target[:200],
                    at=self.now(),
                )
            )

    async def activity(
        self, workspace_id: str, *, limit: int, before: datetime | None = None
    ) -> list[TeamEvent]:
        """Newest first, a page at a time."""
        query = select(TeamEvent).where(TeamEvent.workspace_id == workspace_id)
        if before is not None:
            query = query.where(TeamEvent.at < before)
        async with self.sessions() as s:
            return list(await s.scalars(query.order_by(TeamEvent.at.desc()).limit(limit)))

    async def _moderator(self, s: AsyncSession, workspace_id: str, user_id: str) -> User:
        user = await s.scalar(
            select(User).where(
                User.id == user_id, User.workspace_id == workspace_id, User.role == "moderator"
            )
        )
        if user is None:
            raise NotFound("There's no such moderator on your team.")
        return user

    async def _send_invite(self, user: User, inviter_id: str) -> None:
        async with self.sessions() as s:
            business = await s.scalar(
                select(Workspace.name).where(Workspace.id == user.workspace_id)
            )
            inviter = await s.scalar(select(User.email).where(User.id == inviter_id))
        await self.users.send_code(user, "invite", business or "", inviter or "")
