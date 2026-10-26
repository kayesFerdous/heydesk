"""The platform's side (D23): every business, every user, and the audit log of what admins
did inside businesses. Only `api/admin.py` and the admin dependency call this."""

import math
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

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
    Turn,
    User,
    Workspace,
)

PAGE = 200
DAY_ZONE = ZoneInfo("Asia/Dhaka")  # the overview's days are Dhaka's: every business is there today
BUSIEST = 5


@dataclass(frozen=True)
class BusinessRow:
    id: str
    name: str
    status: str
    suspended_reason: str
    kind: str | None  # its desk; None: no agent yet
    owner_email: str | None
    conversations: int
    last_activity_at: datetime | None
    created_at: datetime
    plan: str  # D106
    paid_until: datetime


@dataclass(frozen=True)
class Owner:
    email: str
    created_at: datetime


@dataclass(frozen=True)
class ConnectedChannel:
    kind: str  # "telegram", "messenger" or "whatsapp"
    address: str  # the bot's username; the Page's id; the number's id
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
    suspended_reason: str  # empty while active
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
    usage: "Totals"  # its turns in the last 7 days (D73)


@dataclass(frozen=True)
class Totals:
    """What the agents did in a stretch of days (D72). Cost counts only priced providers."""

    turns: int
    conversations: int  # started in those days
    input_tokens: int
    cached_tokens: int  # of the input: the share Groq's prompt cache served
    output_tokens: int
    cost_micros: int
    failed: int  # turns with an error: the visitor got the polite line
    handed_off: int
    first_reply_ms: int | None  # the median turn's first words
    slow_first_reply_ms: int | None  # the 95th percentile's


@dataclass(frozen=True)
class DayRow:
    day: date
    turns: int
    conversations: int
    cost_micros: int
    failed: int
    handed_off: int


@dataclass(frozen=True)
class BusyBusiness:
    id: str
    name: str
    turns: int
    cost_micros: int


@dataclass(frozen=True)
class Overview:
    days: int
    today: Totals
    period: Totals
    per_day: list[DayRow]  # oldest first, every day even an empty one
    busiest: list[BusyBusiness]


@dataclass(frozen=True)
class FailedTurn:
    """A turn that ended with the polite line (D73): when, where, and the error. No words."""

    id: str
    at: datetime
    workspace_id: str
    business_name: str
    business_status: str
    conversation_id: str
    channel: str
    llm: str
    error: str


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
            .where(User.role == "owner")  # not a moderator (D97)
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
            BusinessRow(
                ws.id,
                ws.name,
                ws.status,
                ws.suspended_reason,
                kind,
                email,
                n or 0,
                last,
                ws.created_at,
                ws.plan,
                ws.paid_until,
            )
            for ws, kind, email, n, last in rows
        ]

    def since(self, days: int) -> datetime:
        """The start of the first of the last `days` Dhaka days, today being the last."""
        today = self.now().astimezone(DAY_ZONE).date()
        return datetime.combine(today - timedelta(days=days - 1), time(), DAY_ZONE).astimezone(UTC)

    async def business(self, workspace_id: str) -> BusinessDetail:
        week_ago = self.since(7)
        chats = select(func.count()).where(Conversation.workspace_id == workspace_id)
        async with self.sessions() as s:
            ws = await s.get(Workspace, workspace_id)
            if ws is None:
                raise NotFound("There's no such business.")
            agent = await s.scalar(select(Agent).where(Agent.workspace_id == workspace_id))
            owners = await s.scalars(
                select(User)
                .where(User.workspace_id == workspace_id, User.role == "owner")
                .order_by(User.created_at)
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
            week = await s.scalars(
                select(Turn).where(Turn.workspace_id == workspace_id, Turn.at >= week_ago)
            )
            return BusinessDetail(
                id=ws.id,
                name=ws.name,
                status=ws.status,
                suspended_reason=ws.suspended_reason,
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
                usage=totals(week.all(), recent or 0),
            )

    async def overview(self, days: int = 7) -> Overview:
        """Today and the last `days` days, from the saved turns (D71). The rows are read and
        added up here: a few thousand a week is fine, and it reads more easily than SQL."""
        today = self.now().astimezone(DAY_ZONE).date()
        first = today - timedelta(days=days - 1)
        start = self.since(days)
        async with self.sessions() as s:
            turns = (await s.execute(select(Turn).where(Turn.at >= start))).scalars().all()
            started = list(
                await s.scalars(
                    select(Conversation.created_at).where(Conversation.created_at >= start)
                )
            )
            by_business = Counter(t.workspace_id for t in turns)
            top = [ws for ws, _ in by_business.most_common(BUSIEST)]
            named = await s.execute(
                select(Workspace.id, Workspace.name).where(Workspace.id.in_(top))
            )
            names = {ws: name for ws, name in named}

        def day_of(at: datetime) -> date:
            return at.astimezone(DAY_ZONE).date()

        per_day = []
        for n in range(days):
            day = first + timedelta(days=n)
            those = [t for t in turns if day_of(t.at) == day]
            per_day.append(
                DayRow(
                    day,
                    len(those),
                    sum(1 for at in started if day_of(at) == day),
                    sum(t.cost_micros for t in those),
                    sum(1 for t in those if t.error),
                    sum(1 for t in those if t.handed_off),
                )
            )
        busiest = [
            BusyBusiness(
                ws,
                names.get(ws, ""),
                by_business[ws],
                sum(t.cost_micros for t in turns if t.workspace_id == ws),
            )
            for ws in top
        ]
        return Overview(
            days=days,
            today=totals(
                [t for t in turns if day_of(t.at) == today],
                sum(1 for at in started if day_of(at) == today),
            ),
            period=totals(turns, len(started)),
            per_day=per_day,
            busiest=busiest,
        )

    async def failed_turns(
        self,
        workspace_id: str | None = None,
        *,
        limit: int = PAGE,
        before: datetime | None = None,
    ) -> list[FailedTurn]:
        """Newest first, every business's or one's. The next page is `before=` the last `at`."""
        query = (
            select(Turn, Workspace.name, Workspace.status)
            .join(Workspace, Workspace.id == Turn.workspace_id)
            .where(Turn.error != "")
        )
        if workspace_id:
            query = query.where(Turn.workspace_id == workspace_id)
        if before:
            query = query.where(Turn.at < before)
        async with self.sessions() as s:
            rows = (await s.execute(query.order_by(Turn.at.desc()).limit(limit))).all()
        return [
            FailedTurn(
                t.id,
                t.at,
                t.workspace_id,
                name,
                status,
                t.conversation_id,
                t.channel,
                t.llm,
                t.error,
            )
            for t, name, status in rows
        ]

    async def set_business_status(self, workspace_id: str, status: str, reason: str = "") -> None:
        """A reason goes with a suspension (D70); reactivating clears it."""
        async with self.sessions() as s, s.begin():
            business = await s.get(Workspace, workspace_id)
            if business is None:
                raise NotFound("There's no such business.")
            business.status = status
            business.suspended_reason = reason.strip() if status == "suspended" else ""

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


def totals(turns: Sequence[Turn], conversations: int) -> Totals:
    firsts = sorted(t.first_output_ms for t in turns if t.first_output_ms is not None)
    return Totals(
        turns=len(turns),
        conversations=conversations,
        input_tokens=sum(t.input_tokens for t in turns),
        cached_tokens=sum(t.cached_tokens for t in turns),
        output_tokens=sum(t.output_tokens for t in turns),
        cost_micros=sum(t.cost_micros for t in turns),
        failed=sum(1 for t in turns if t.error),
        handed_off=sum(1 for t in turns if t.handed_off),
        first_reply_ms=percentile(firsts, 50),
        slow_first_reply_ms=percentile(firsts, 95),
    )


def percentile(ordered: list[int], p: int) -> int | None:
    """The value p% of them are at or under (nearest rank). None for no values."""
    if not ordered:
        return None
    return ordered[max(0, math.ceil(p / 100 * len(ordered)) - 1)]
