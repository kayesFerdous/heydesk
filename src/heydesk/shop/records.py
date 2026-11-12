"""The records every chat can make that aren't products or orders (`RecordStore`): messages
for the team, call backs, complaints, missed questions, notes on a visitor, and the
numbering every record shares. `shop/store.py` adds the products and orders; the tools that
use only this part are in `shop/tools/inbox.py`.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from pydantic import BaseModel
from sqlalchemy import ScalarResult, String, and_, cast, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from heydesk.domain.values import Confirmed, parse_code
from heydesk.errors import HeyDeskError, InvalidTransition, NotFound
from heydesk.models import (
    Conversation,
    Lead,
    MissedQuestion,
    TeamMessage,
    Ticket,
    Visitor,
    Workspace,
)
from heydesk.models.customer_request import CustomerRequest, next_number
from heydesk.ports.knowledge_store import Snippet
from heydesk.runtime.text import one_line

PAGE = 100  # the most rows a list returns at once (newest first)
BROWSER_CHANNELS = ("web", "voice")  # one browser's token is the contact on both
MAX_NOTES = 20  # what the agent remembers about one visitor, at most


class KnowledgeSearch(Protocol):
    """What the store needs to search the business's documents. The shop never imports
    `services/`: the service that does this is handed in."""

    async def search(self, workspace_id: str, question: str) -> list[Snippet]: ...


class PhotoImporter(Protocol):
    """What the store needs to turn a product list's photo links into saved photos."""

    async def import_links(self, workspace_id: str, links: list[str]) -> dict[str, str]: ...


class TooLate(ValueError):
    """An order moved on (the courier has it) since the visitor asked to
    change it."""


@dataclass(frozen=True)
class NewMessage:
    """A message for the team, ready to save once the visitor said yes to it."""

    body: str
    name: str
    phone: str
    best_time: str = ""


@dataclass(frozen=True)
class NewLead:
    """A call back the visitor asked for, ready to save once they said yes to it."""

    call_at: datetime  # UTC
    note: str
    name: str
    phone: str


class RecordStore:
    """One business's data, for its tools and its owner's routes (rule 6: every query is
    scoped to `workspace_id`). `ShopStore` subclasses this and adds the shop's tables.

    Tools get one made for their conversation, so what they save is linked to it."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        workspace_id: str,
        now: Callable[[], datetime],
        *,
        conversation_id: str | None = None,
        knowledge: KnowledgeSearch | None = None,
        public_url: str = "",
        photos: PhotoImporter | None = None,
    ) -> None:
        self.sessions = sessions
        self.workspace_id = workspace_id
        self.now = now
        self.conversation_id = conversation_id
        self.knowledge = knowledge
        self.public_url = public_url  # where a link in a reply points: the payment page
        self.photos = photos  # for the photo links in a shop's list

    def pay_link(self, record_id: str) -> str:
        """The visitor's link to pay for something saved unpaid. It starts a new payment
        each time, so it doesn't run out; the id is long and random, so no one guesses another."""
        return f"{self.public_url}/pay/{record_id}"

    async def search_knowledge(self, question: str) -> list[Snippet]:
        if self.knowledge is None:
            return []
        return await self.knowledge.search(self.workspace_id, question)

    # Messages for the team

    async def save_message(self, message: Confirmed[NewMessage]) -> str:
        """Saves it and returns its code: "M-104"."""
        m = message.value
        saved = await self.add(
            TeamMessage, name=m.name, phone=m.phone, body=m.body, best_time=m.best_time
        )
        return saved.code

    async def save_missed_question(self, question: str) -> None:
        """A question it couldn't answer, for the owner to teach."""
        question = one_line(question, 300)
        if not question:
            return
        async with self.sessions() as s, s.begin():
            s.add(
                MissedQuestion(
                    workspace_id=self.workspace_id,
                    conversation_id=self.conversation_id,
                    question=question,
                    created_at=self.now(),
                )
            )

    async def missed_questions(self) -> list[MissedQuestion]:
        """The questions not taught yet, oldest first, the latest 500."""
        async with self.sessions() as s:
            rows = await s.scalars(
                select(MissedQuestion)
                .where(
                    MissedQuestion.workspace_id == self.workspace_id,
                    MissedQuestion.taught_at.is_(None),
                )
                .order_by(MissedQuestion.created_at.desc())
                .limit(500)
            )
            return list(reversed(rows.all()))

    async def mark_answered(self, ids: list[str]) -> None:
        """These questions have an answer now: off the list."""
        async with self.sessions() as s, s.begin():
            rows = await s.scalars(
                select(MissedQuestion).where(
                    MissedQuestion.workspace_id == self.workspace_id, MissedQuestion.id.in_(ids)
                )
            )
            for row in rows:
                row.taught_at = self.now()

    async def save_lead(self, lead: Confirmed[NewLead]) -> str:
        """Saves the call back, open until the team taps how it went, and returns "L-101"."""
        a = lead.value
        saved = await self.add(
            Lead, status="open", name=a.name, phone=a.phone, call_at=a.call_at, note=a.note
        )
        return saved.code

    async def open_ticket(self, reason: str) -> tuple[Ticket, bool]:
        """A complaint in this chat: its open ticket, or a new one. No yes is asked:
        it's the team's to-do, like a hand-off, not something the visitor is promised. Says
        whether it's new."""
        async with self.sessions() as s:
            found = await s.scalar(
                select(Ticket).where(
                    Ticket.workspace_id == self.workspace_id,
                    Ticket.conversation_id == self.conversation_id,
                    Ticket.status == "open",
                )
            )
        if found is not None:
            return found, False
        reason = one_line(reason, 300) or "complaint"
        return await self.add(Ticket, status="open", name="", phone="", reason=reason), True

    # The products: replaced whole, kept in order

    async def all_rows[M: BaseModel](self, table: type[Any], model: type[M]) -> list[M]:
        """Every row of `table` (it has a `position`) as `model`, in the business's order."""
        async with self.sessions() as s:
            rows: ScalarResult[Any] = await s.scalars(
                select(table)
                .where(table.workspace_id == self.workspace_id)
                .order_by(table.position)
            )
            return [model.model_validate(row, from_attributes=True) for row in rows]

    async def replace_rows(self, table: type[Any], items: Sequence[BaseModel]) -> None:
        """`table` becomes exactly these, in this order."""
        async with self.sessions() as s, s.begin():
            await self._lock(s)  # an order taking stock meanwhile would lose its take
            await self._replace(s, table, items)

    async def _replace(self, s: AsyncSession, table: type[Any], items: Sequence[BaseModel]) -> None:
        await s.execute(delete(table).where(table.workspace_id == self.workspace_id))
        s.add_all(
            table(workspace_id=self.workspace_id, position=n, **item.model_dump())
            for n, item in enumerate(items)
        )

    # Shared by every table of things taken (`models.customer_request`)

    async def add[T: CustomerRequest](self, table: type[T], **fields: Any) -> T:
        """Saves a new one with the business's next number."""
        async with self.sessions() as s, s.begin():
            await self._lock(s)
            row = await self._new_row(s, table, **fields)
        return row

    async def _new_row[T: CustomerRequest](
        self, s: AsyncSession, table: type[T], **fields: Any
    ) -> T:
        """A new row with the business's next number, linked to this conversation. The
        caller holds the lock (`_lock`), so two at once don't get the same number."""
        now = self.now()
        row = table(
            workspace_id=self.workspace_id,
            conversation_id=self.conversation_id,
            number=await next_number(s, table, self.workspace_id),
            created_at=now,
            updated_at=now,
            **fields,
        )
        s.add(row)
        return row

    async def find[T: CustomerRequest](
        self,
        table: type[T],
        status: str | None = None,
        q: str | None = None,
        *,
        limit: int = PAGE,
        before: datetime | None = None,
    ) -> list[T]:
        """Newest first. `q` matches the number or code ("A-104"), the name or the phone.

        One page at a time: up to `limit` rows made before `before`. The next page is
        `before=` the last row's `created_at`. Two rows made in the same microsecond, split
        across a page edge, would hide one of them: not worth a second cursor field."""
        query = select(table).where(table.workspace_id == self.workspace_id)
        if before:
            query = query.where(table.created_at < before)
        if status:
            query = query.where(table.status == status)
        if q := (q or "").strip():
            like = f"%{q}%"
            code = parse_code(q)
            query = query.where(
                or_(
                    table.name.ilike(like),
                    table.phone.ilike(like),
                    cast(table.number, String).like(like),
                    table.number == (code[1] if code else -1),
                )
            )
        async with self.sessions() as s:
            return list(await s.scalars(query.order_by(table.created_at.desc()).limit(limit)))

    async def records_in_conversation[T: CustomerRequest](self, table: type[T]) -> list[T]:
        """What was taken in this conversation, newest first: the visitor needs no code
        for it."""
        if self.conversation_id is None:
            return []
        async with self.sessions() as s:
            found = await s.scalars(
                select(table)
                .where(
                    table.workspace_id == self.workspace_id,
                    table.conversation_id == self.conversation_id,
                )
                .order_by(table.created_at.desc())
            )
            return list(found)

    async def earlier_records[T: CustomerRequest](self, table: type[T], limit: int = 1) -> list[T]:
        """What this visitor had before, newest first: in this conversation or an
        earlier one from the same chat on a messaging app, or the same browser on the web chat
        and its calls. A second order in the same chat is offered the first one's details too.
        Only this conversation's when it has no `contact`."""
        workspace_id = self.workspace_id
        async with self.sessions() as s:
            here = await self._current_conversation(s)
            if here is None:
                return []
            if here.contact is None:
                same = table.conversation_id == here.id
            else:
                channels = BROWSER_CHANNELS if here.channel in BROWSER_CHANNELS else (here.channel,)
                same = and_(
                    Conversation.channel.in_(channels), Conversation.contact == here.contact
                )
            found = await s.scalars(
                select(table)
                .join(Conversation, table.conversation_id == Conversation.id)
                .where(
                    table.workspace_id == workspace_id,
                    Conversation.workspace_id == workspace_id,
                    same,
                )
                .order_by(table.created_at.desc())
                .limit(limit)
            )
            return list(found)

    # What the agent remembers about a visitor: kept on their `Visitor` row

    async def visitor_notes(self) -> dict[str, str]:
        """The notes on this visitor, `{about: note}`, oldest first. Empty without a contact."""
        async with self.sessions() as s:
            visitor = await self._visitor(s)
            return dict(visitor.notes) if visitor else {}

    async def remember(self, about: str, note: str) -> bool:
        """Keeps `note` under `about`, replacing what was there; the oldest go past
        MAX_NOTES. False when there's no visitor to keep it for."""
        async with self.sessions() as s, s.begin():
            visitor = await self._visitor(s)
            if visitor is None:
                return False
            notes = {k: v for k, v in visitor.notes.items() if k != about}
            notes[about] = note
            visitor.notes = dict(list(notes.items())[-MAX_NOTES:])  # a new value, or it isn't saved
        return True

    async def _current_conversation(self, s: AsyncSession) -> Conversation | None:
        if self.conversation_id is None:
            return None
        found: Conversation | None = await s.scalar(
            select(Conversation).where(
                Conversation.workspace_id == self.workspace_id,
                Conversation.id == self.conversation_id,
            )
        )
        return found

    async def _visitor(self, s: AsyncSession) -> Visitor | None:
        """This conversation's visitor, keyed as the limits key them: `web:<token>` for
        the web chat and calls, `messenger:<chat_id>` and so on."""
        here = await self._current_conversation(s)
        if here is None or here.contact is None:
            return None
        channel = "web" if here.channel in BROWSER_CHANNELS else here.channel
        found: Visitor | None = await s.scalar(
            select(Visitor).where(
                Visitor.workspace_id == self.workspace_id,
                Visitor.key == f"{channel}:{here.contact}",
            )
        )
        return found

    async def get[T: CustomerRequest](self, table: type[T], row_id: str) -> T:
        async with self.sessions() as s:
            return await self._get_row(s, table, row_id)

    async def by_number[T: CustomerRequest](self, table: type[T], number: int) -> T | None:
        async with self.sessions() as s:
            found: T | None = await s.scalar(
                select(table).where(table.workspace_id == self.workspace_id, table.number == number)
            )
            return found

    async def set_status[T: CustomerRequest](self, table: type[T], row_id: str, status: str) -> T:
        async with self.sessions() as s, s.begin():
            row = await self._get_row(s, table, row_id)
            self._move(row, status)
        return row

    async def lead_outcome(self, lead_id: str, status: str, why: str) -> Lead:
        """The team tapped how a call back went; a lost one keeps its why."""
        async with self.sessions() as s, s.begin():
            lead = await self._get_row(s, Lead, lead_id)
            self._move(lead, status)
            lead.why = one_line(why, 200).lower() if status == "lost" else ""
        return lead

    async def lost_reasons(self) -> list[tuple[str, int]]:
        """Why sales were lost: each lost lead's why, with how many, most first."""
        async with self.sessions() as s:
            rows = await s.execute(
                select(Lead.why, func.count())
                .where(Lead.workspace_id == self.workspace_id, Lead.status == "lost")
                .group_by(Lead.why)
                .order_by(func.count().desc())
            )
            return [(why or "no reason given", n) for why, n in rows.all()]

    async def _lock(self, s: AsyncSession) -> None:
        """Waits until no other transaction is numbering this business's records or changing
        its stock, and holds them off until this one ends. Two saves at the same moment would
        otherwise both read the same highest number, or both take the last one. Postgres
        locks the business's row; SQLite writes one transaction at a time anyway."""
        await s.execute(
            select(Workspace.id).where(Workspace.id == self.workspace_id).with_for_update()
        )

    def _move(self, row: CustomerRequest, status: str) -> None:
        """`row.move`, with its problems as the errors the owner's routes return."""
        try:
            row.move(status, self.now())
        except LookupError as error:
            raise HeyDeskError(str(error)) from None
        except ValueError as error:
            raise InvalidTransition(str(error)) from None

    async def _get_row[T: CustomerRequest](self, s: AsyncSession, table: type[T], row_id: str) -> T:
        row: T | None = await s.scalar(
            select(table).where(table.id == row_id, table.workspace_id == self.workspace_id)
        )
        if row is None:
            raise NotFound("There's no such record.")
        return row
