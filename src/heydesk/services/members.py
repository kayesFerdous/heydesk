"""A business's team (D97): the owner adds moderators by email, and sees what everyone did.

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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from heydesk.errors import Conflict, NotFound
from heydesk.models import TeamEvent, User, Workspace
from heydesk.services.billing import PLANS, count_moderators
from heydesk.services.users import Principal, UserService


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

    async def members(self, ws: str) -> list[User]:
        """The owners, then the moderators ("owner" sorts after "moderator"), oldest first."""
        async with self.sessions() as s:
            query = select(User).where(User.workspace_id == ws)
            return list(await s.scalars(query.order_by(User.role.desc(), User.created_at)))

    async def invite(self, ws: str, email: str, inviter_id: str) -> User:
        """A new moderator, and an email with the code to join."""
        email = email.strip().lower()
        async with self.sessions() as s, s.begin():
            taken = await s.scalar(select(User).where(User.email == email))
            if taken is not None:
                raise Conflict(
                    "They're already on your team."
                    if taken.workspace_id == ws
                    else "That email already has a HeyDesk account, so it can't join your team."
                )
            moderators = await count_moderators(s, ws)
            business = await s.get(Workspace, ws)
            seats = PLANS[business.plan].moderators if business else 0  # the plan's (D106)
            if moderators >= seats:
                raise Conflict(
                    f"Your plan has {seats} moderator{'s' if seats > 1 else ''}. "
                    "Choose a bigger plan on the billing page to add more."
                )
            password = secrets.token_urlsafe(32)  # no one knows it: they set their own to join
            user = await self.users.new_user(
                s, email, password, role="moderator", ws=ws, verified=False
            )
        await self._send_invite(user, inviter_id)
        return user

    async def resend(self, ws: str, user_id: str, inviter_id: str) -> None:
        """The invite again, with a new code; a 409 once they've joined."""
        async with self.sessions() as s:
            user = await self._moderator(s, ws, user_id)
        if user.email_verified_at is not None:
            raise Conflict("They've already joined.")
        await self._send_invite(user, inviter_id)

    async def remove(self, ws: str, user_id: str) -> None:
        """A moderator leaves the team, invited or joined. An owner can't be removed."""
        async with self.sessions() as s, s.begin():
            await s.delete(await self._moderator(s, ws, user_id))

    async def record(self, principal: Principal, action: str, target: str) -> None:
        assert principal.workspace_id is not None
        async with self.sessions() as s, s.begin():
            email = await s.scalar(select(User.email).where(User.id == principal.user_id))
            s.add(
                TeamEvent(
                    workspace_id=principal.workspace_id,
                    actor_id=principal.user_id,
                    actor_email=email or "",
                    action=action[:120],
                    target=target[:200],
                    at=self.now(),
                )
            )

    async def activity(
        self, ws: str, *, limit: int, before: datetime | None = None
    ) -> list[TeamEvent]:
        """Newest first, a page at a time (D46)."""
        query = select(TeamEvent).where(TeamEvent.workspace_id == ws)
        if before is not None:
            query = query.where(TeamEvent.at < before)
        async with self.sessions() as s:
            return list(await s.scalars(query.order_by(TeamEvent.at.desc()).limit(limit)))

    async def _moderator(self, s: AsyncSession, ws: str, user_id: str) -> User:
        user = await s.scalar(
            select(User).where(
                User.id == user_id, User.workspace_id == ws, User.role == "moderator"
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
