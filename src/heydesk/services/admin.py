"""The platform's side (D23): every business, every user, and the audit log of what admins
did inside businesses. Only `api/admin.py` and the admin dependency call this."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from heydesk.errors import NotFound
from heydesk.models import (
    Agent,
    AuditEvent,
    Channel,
    Conversation,
    Document,
    PaymentAccount,
    SheetLink,
    User,
    Workspace,
)

PAGE = 200


@dataclass(frozen=True)
class BusinessRow:
    id: str
    name: str
    status: str
    kind: str | None  # its desk; None: no agent yet
    owner_email: str | None
    conversations: int
    last_activity_at: datetime | None
    created_at: datetime


@dataclass(frozen=True)
class Owner:
    email: str
    created_at: datetime


@dataclass(frozen=True)
class ConnectedChannel:
    kind: str  # "telegram" or "messenger"
    address: str  # the bot's username; the Page's id
    created_at: datetime


@dataclass(frozen=True)
class Sheet:
    synced_at: datetime | None  # the last good read
    problem: str  # empty: fine


@dataclass(frozen=True)
class BusinessDetail:
    """One business at a glance, for the admin (no customer's words, only counts)."""

    id: str
    name: str
    status: str
    kind: str | None  # its desk; None: no agent yet
    agent_name: str | None
    public_key: str | None  # the chat link's key
    created_at: datetime
    owners: list[Owner]
    channels: list[ConnectedChannel]
    payments: str | None  # "sandbox" or "live"; None: not connected
    documents: int
    sheet: Sheet | None
    conversations: int
    conversations_7_days: int
    waiting: int  # handed to a person and not yet answered
    last_activity_at: datetime | None


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
            select(Workspace, Agent.kind, owner.c.email, chats.c.n, chats.c.last)
            .outerjoin(Agent, Agent.workspace_id == Workspace.id)
            .outerjoin(owner, owner.c.workspace_id == Workspace.id)
            .outerjoin(chats, chats.c.workspace_id == Workspace.id)
            .order_by(Workspace.created_at.desc())
            .limit(PAGE)
        )
        async with self.sessions() as s:
            rows = (await s.execute(query)).all()
        return [
            BusinessRow(ws.id, ws.name, ws.status, kind, email, n or 0, last, ws.created_at)
            for ws, kind, email, n, last in rows
        ]

    async def business(self, workspace_id: str) -> BusinessDetail:
        week_ago = self.now() - timedelta(days=7)
        chats = select(func.count()).where(Conversation.workspace_id == workspace_id)
        async with self.sessions() as s:
            ws = await s.get(Workspace, workspace_id)
            if ws is None:
                raise NotFound("There's no such business.")
            agent = await s.scalar(select(Agent).where(Agent.workspace_id == workspace_id))
            owners = await s.scalars(
                select(User).where(User.workspace_id == workspace_id).order_by(User.created_at)
            )
            channels = await s.scalars(
                select(Channel)
                .where(Channel.workspace_id == workspace_id)
                .order_by(Channel.created_at)
            )
            account = await s.scalar(
                select(PaymentAccount).where(PaymentAccount.workspace_id == workspace_id)
            )
            sheet = await s.scalar(select(SheetLink).where(SheetLink.workspace_id == workspace_id))
            documents = await s.scalar(
                select(func.count()).where(Document.workspace_id == workspace_id)
            )
            total = await s.scalar(chats)
            recent = await s.scalar(chats.where(Conversation.created_at >= week_ago))
            waiting = await s.scalar(chats.where(Conversation.status == "needs_human"))
            last = await s.scalar(
                select(func.max(Conversation.updated_at)).where(
                    Conversation.workspace_id == workspace_id
                )
            )
            return BusinessDetail(
                id=ws.id,
                name=ws.name,
                status=ws.status,
                kind=agent.kind if agent else None,
                agent_name=agent.name if agent else None,
                public_key=agent.public_key if agent else None,
                created_at=ws.created_at,
                owners=[Owner(u.email, u.created_at) for u in owners],
                channels=[ConnectedChannel(c.kind, c.address, c.created_at) for c in channels],
                payments=("sandbox" if account.sandbox else "live") if account else None,
                documents=documents or 0,
                sheet=Sheet(sheet.synced_at, sheet.problem) if sheet else None,
                conversations=total or 0,
                conversations_7_days=recent or 0,
                waiting=waiting or 0,
                last_activity_at=last,
            )

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

    async def audit_log(
        self, workspace_id: str | None = None, *, limit: int = PAGE, before: datetime | None = None
    ) -> list[tuple[AuditEvent, str]]:
        """Newest first, with the admin's email. The next page is `before=` the last `at`."""
        query = select(AuditEvent, User.email).join(User, User.id == AuditEvent.actor_id)
        if workspace_id:
            query = query.where(AuditEvent.workspace_id == workspace_id)
        if before:
            query = query.where(AuditEvent.at < before)
        async with self.sessions() as s:
            rows = (await s.execute(query.order_by(AuditEvent.at.desc()).limit(limit))).all()
        return [(event, email) for event, email in rows]
