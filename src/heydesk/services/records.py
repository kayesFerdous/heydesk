"""Records (D24): what finished jobs leave for the business. Written by tools only with a
`Confirmed[...]` (rule 2), already scoped to one conversation; read and moved along by the inbox.

One table for orders and messages for the team. The tools still place an order or submit a
request through their ports (`runtime.tool.Orders`, `Requests`) and can't tell they land together.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from heydesk.domain.values import Confirmed, NewOrder, NewRequest
from heydesk.errors import HeyDeskError, InvalidTransition, NotFound
from heydesk.models import Record
from heydesk.models.record import FIRST_STATUS
from heydesk.runtime.tasks import Task

FIRST_NUMBER = 101  # a business's first order; "order 1" sounds like a test
PAGE = 100  # newest first


@dataclass(frozen=True)
class InboxEntry:
    """One job in the sidebar."""

    job: str
    title: str
    kind: str
    waiting: int  # new orders or messages
    total: int


class RecordService:
    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], now: Callable[[], datetime]
    ) -> None:
        self.sessions = sessions
        self.now = now

    # The inbox

    async def find(
        self,
        ws: str,
        *,
        job: str | None = None,
        status: str | None = None,
        q: str | None = None,
        conversation_id: str | None = None,
    ) -> list[Record]:
        """Newest first. `q` matches the number, name or phone."""
        query = select(Record).where(Record.workspace_id == ws)
        if job:
            query = query.where(Record.job == job)
        if status:
            query = query.where(Record.status == status)
        if conversation_id:
            query = query.where(Record.conversation_id == conversation_id)
        if q := (q or "").strip():
            like = f"%{q}%"
            query = query.where(
                or_(
                    Record.customer_name.ilike(like),
                    Record.customer_phone.ilike(like),
                    cast(Record.number, String).like(like),
                )
            )
        async with self.sessions() as s:
            return list(await s.scalars(query.order_by(Record.created_at.desc()).limit(PAGE)))

    async def get(self, ws: str, record_id: str) -> Record:
        async with self.sessions() as s:
            record = await s.scalar(
                select(Record).where(Record.id == record_id, Record.workspace_id == ws)
            )
        if record is None:
            raise NotFound("There's no such record.")
        return record

    async def set_status(self, ws: str, record_id: str, status: str) -> Record:
        async with self.sessions() as s, s.begin():
            record = await s.scalar(
                select(Record).where(Record.id == record_id, Record.workspace_id == ws)
            )
            if record is None:
                raise NotFound("There's no such record.")
            try:
                record.move(status, self.now())
            except LookupError as error:
                raise HeyDeskError(str(error)) from None
            except ValueError as error:
                raise InvalidTransition(str(error)) from None
        return record

    async def inbox(self, ws: str, tasks: dict[str, Task]) -> list[InboxEntry]:
        """The agent's jobs (its preset's tasks), then any job that has records but is no
        longer a task, so old records are never hidden."""
        async with self.sessions() as s:
            rows = (
                await s.execute(
                    select(Record.job, Record.kind, Record.status).where(Record.workspace_id == ws)
                )
            ).all()
        entries = {job: (task.label.capitalize(), task.kind) for job, task in tasks.items()}
        for job, kind, _ in rows:
            entries.setdefault(job, (job.capitalize(), kind))
        counts: dict[str, list[int]] = {job: [0, 0] for job in entries}
        for job, _, status in rows:
            counts[job][0] += status == FIRST_STATUS
            counts[job][1] += 1
        return [
            InboxEntry(job, title, kind, *counts[job]) for job, (title, kind) in entries.items()
        ]

    # What tools get: writing only, already scoped to one conversation (rule 6)

    def for_conversation(
        self, ws: str, agent_id: str, conversation_id: str
    ) -> "ConversationRecords":
        return ConversationRecords(self, ws, agent_id, conversation_id)

    async def place(
        self, ws: str, agent_id: str, conversation_id: str, order: Confirmed[NewOrder]
    ) -> int:
        """Saves the order with the business's next order number, and returns the number."""
        o = order.value
        record = await self._add(
            ws,
            agent_id,
            conversation_id,
            job=o.task,
            kind="order",
            customer_name=o.customer_name,
            customer_phone=o.customer_phone,
            note=o.note,
            lines=o.lines,
            total=o.total,
            currency=o.currency,
            due_at=o.pickup_time,
        )
        return record.number

    async def submit(
        self, ws: str, agent_id: str, conversation_id: str, request: Confirmed[NewRequest]
    ) -> str:
        """Saves the message for the team and returns its id."""
        r = request.value
        record = await self._add(
            ws,
            agent_id,
            conversation_id,
            job=r.task,
            kind="request",
            customer_name=r.name,
            customer_phone=r.phone,
            details=r.details,
        )
        return record.id

    async def _add(self, ws: str, agent_id: str, conversation_id: str, **fields: object) -> Record:
        kind = str(fields["kind"])
        async with self.sessions() as s, s.begin():
            last = await s.scalar(
                select(func.max(Record.number)).where(
                    Record.workspace_id == ws, Record.kind == kind
                )
            )
            number = last + 1 if last else FIRST_NUMBER
            now = self.now()
            record = Record(
                workspace_id=ws,
                agent_id=agent_id,
                conversation_id=conversation_id,
                number=number,
                created_at=now,
                updated_at=now,
                **fields,
            )
            s.add(record)
        return record


@dataclass(frozen=True)
class ConversationRecords:
    """One conversation's records, for `place_order` and `submit_request`."""

    service: RecordService
    workspace_id: str
    agent_id: str
    conversation_id: str

    async def place(self, order: Confirmed[NewOrder]) -> int:
        return await self.service.place(
            self.workspace_id, self.agent_id, self.conversation_id, order
        )

    async def submit(self, request: Confirmed[NewRequest]) -> str:
        return await self.service.submit(
            self.workspace_id, self.agent_id, self.conversation_id, request
        )
