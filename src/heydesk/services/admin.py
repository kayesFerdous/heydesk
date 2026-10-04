"""The platform's side (D23): every business, every user, and the audit log of what admins
did inside businesses. Only `api/admin.py` and the admin dependency call this."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from heydesk.errors import NotFound
from heydesk.models import Agent, AuditEvent, Conversation, User, Workspace

PAGE = 200


@dataclass(frozen=True)
class BusinessRow:
    id: str
    name: str
    status: str
    preset: str | None  # None: no agent yet
    owner_email: str | None
    conversations: int
    last_activity_at: datetime | None
    created_at: datetime


class AdminService:
    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], now: Callable[[], datetime]
    ) -> None:
        self.sessions = sessions
        self.now = now

    async def audit(
        self, actor_id: str, workspace_id: str | None, action: str, target: str
    ) -> None:
        async with self.sessions() as s, s.begin():
            s.add(
                AuditEvent(
                    actor_id=actor_id,
                    workspace_id=workspace_id,
                    action=action[:120],
                    target=target[:200],
                    at=self.now(),
                )
            )

    async def require_business(self, workspace_id: str) -> None:
        async with self.sessions() as s:
            if await s.get(Workspace, workspace_id) is None:
                raise NotFound("There's no such business.")

    async def businesses(self) -> list[BusinessRow]:
        """Newest first."""
        chats = (
            select(
                Conversation.workspace_id,
                func.count().label("n"),
                func.max(Conversation.updated_at).label("last"),
            )
            .group_by(Conversation.workspace_id)
            .subquery()
        )
        owner = (
            select(User.workspace_id, func.min(User.email).label("email"))
            .group_by(User.workspace_id)
            .subquery()
        )
        query = (
            select(Workspace, Agent.preset, owner.c.email, chats.c.n, chats.c.last)
            .outerjoin(Agent, Agent.workspace_id == Workspace.id)
            .outerjoin(owner, owner.c.workspace_id == Workspace.id)
            .outerjoin(chats, chats.c.workspace_id == Workspace.id)
            .order_by(Workspace.created_at.desc())
            .limit(PAGE)
        )
        async with self.sessions() as s:
            rows = (await s.execute(query)).all()
        return [
            BusinessRow(ws.id, ws.name, ws.status, preset, email, n or 0, last, ws.created_at)
            for ws, preset, email, n, last in rows
        ]

    async def set_business_status(self, workspace_id: str, status: str) -> None:
        async with self.sessions() as s, s.begin():
            business = await s.get(Workspace, workspace_id)
            if business is None:
                raise NotFound("There's no such business.")
            business.status = status

    async def users(self) -> list[tuple[User, str | None]]:
        """Every user with their business's name; newest first."""
        query = (
            select(User, Workspace.name)
            .outerjoin(Workspace, Workspace.id == User.workspace_id)
            .order_by(User.created_at.desc())
            .limit(PAGE)
        )
        async with self.sessions() as s:
            return [(user, name) for user, name in (await s.execute(query)).all()]

    async def audit_log(self, workspace_id: str | None = None) -> list[tuple[AuditEvent, str]]:
        """Newest first, with the admin's email."""
        query = select(AuditEvent, User.email).join(User, User.id == AuditEvent.actor_id)
        if workspace_id:
            query = query.where(AuditEvent.workspace_id == workspace_id)
        async with self.sessions() as s:
            rows = (await s.execute(query.order_by(AuditEvent.at.desc()).limit(PAGE))).all()
        return [(event, email) for event, email in rows]
