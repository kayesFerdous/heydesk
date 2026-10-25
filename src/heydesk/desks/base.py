"""What a desk is: one kind of business (a clinic, a restaurant), written as plain Python.

A desk owns everything that makes that business different: its config fields, its prompt
(who the agent is and how it thinks), its tools, its tables and its owner routes. What every
desk shares lives here (`Store`: messages for the team, the documents search) and in
`desks/shared.py` (the shared tools).
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, ClassVar

from pydantic import BaseModel
from sqlalchemy import ScalarResult, String, and_, cast, delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from heydesk.desks.csv_import import Column
from heydesk.domain.values import Confirmed, parse_code
from heydesk.errors import Conflict, HeyDeskError, InvalidTransition, NotFound
from heydesk.models import (
    Conversation,
    PaymentAccount,
    SheetLink,
    TeamMessage,
    Visitor,
    WooLink,
)
from heydesk.models.taken import Taken, next_number
from heydesk.ports.knowledge_store import Snippet
from heydesk.runtime.config import AgentConfig
from heydesk.runtime.tool import Tool

if TYPE_CHECKING:  # only a type here: services import the desks
    from heydesk.services.knowledge import KnowledgeService
    from heydesk.services.photos import PhotoService

PAGE = 100  # the most rows a list returns at once (newest first)
BROWSER = ("web", "voice")  # one browser's token is the contact on both (D114)
MAX_NOTES = 20  # what the agent remembers about one visitor, at most (D124)


class SheetLinked(Conflict):
    code = "sheet_linked"

    def __init__(self) -> None:
        super().__init__(
            "Your list comes from your Google Sheet or WooCommerce store: change it there."
        )


class TooLate(ValueError):
    """An order moved on (the courier or the kitchen has it) since the visitor asked to
    change it."""


@dataclass(frozen=True)
class NewMessage:
    """A message for the team, ready to save once the visitor said yes to it."""

    body: str
    name: str
    phone: str
    best_time: str = ""


class Store:
    """One business's data, for its desk's tools and its owner's routes (rule 6: every query
    is scoped to `workspace_id`). A desk's store subclasses this and adds its own tables.

    Tools get one made for their conversation, so what they save is linked to it."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        workspace_id: str,
        now: Callable[[], datetime],
        *,
        conversation_id: str | None = None,
        knowledge: "KnowledgeService | None" = None,
        public_url: str = "",
        photos: "PhotoService | None" = None,
    ) -> None:
        self.sessions = sessions
        self.workspace_id = workspace_id
        self.now = now
        self.conversation_id = conversation_id
        self.knowledge = knowledge
        self.public_url = public_url  # where a link in a reply points: the payment page
        self.photos = photos  # for the photo links in a shop's list (D102)

    async def pays_first(self, config: AgentConfig) -> bool:
        """Whether the agent asks for payment before placing anything: the owner turned it on
        (`pay_first`) and connected the business's SSLCommerz store or bKash account (D49,
        D109). Without either, the agent takes orders as if it were off."""
        if not getattr(config, "pay_first", False):
            return False
        async with self.sessions() as s:
            found = await s.scalar(
                select(PaymentAccount.id).where(PaymentAccount.workspace_id == self.workspace_id)
            )
        return found is not None

    def pay_link(self, record_id: str) -> str:
        """The visitor's link to pay for something saved unpaid (D47). It starts a new payment
        each time, so it doesn't run out; the id is long and random, so no one guesses another."""
        return f"{self.public_url}/pay/{record_id}"

    async def search_knowledge(self, question: str) -> list[Snippet]:
        if self.knowledge is None:
            return []
        return await self.knowledge.search(self.workspace_id, question)

    # Messages for the team: every desk takes them

    async def save_message(self, message: Confirmed[NewMessage]) -> str:
        """Saves it and returns its code: "M-104"."""
        m = message.value
        saved = await self.add(
            TeamMessage, name=m.name, phone=m.phone, body=m.body, best_time=m.best_time
        )
        return saved.code

    async def messages(
        self,
        status: str | None = None,
        q: str | None = None,
        *,
        limit: int = PAGE,
        before: datetime | None = None,
    ) -> list[TeamMessage]:
        return await self.find(TeamMessage, status, q, limit=limit, before=before)

    # A desk's list (the menu, the doctors, the products): replaced whole, kept in order

    async def rows_of[M: BaseModel](self, table: type[Any], model: type[M]) -> list[M]:
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
            await self._replace(s, table, items)

    async def _replace(self, s: AsyncSession, table: type[Any], items: Sequence[BaseModel]) -> None:
        await s.execute(delete(table).where(table.workspace_id == self.workspace_id))
        s.add_all(
            table(workspace_id=self.workspace_id, position=n, **item.model_dump())
            for n, item in enumerate(items)
        )

    async def take_sheet(
        self, text: str, mapping: dict[str, str], seen: dict[str, Any]
    ) -> dict[str, Any]:
        """The list becomes the linked sheet's rows (D58); returns what to remember for the
        next time (`seen`). Raises InvalidConfig with the rows' problems, saving nothing."""
        raise NotFound("This agent has no list to keep in a sheet.")

    async def linked(self) -> bool:
        """A Google Sheet (D58) or a WooCommerce store (D75) is the list."""
        async with self.sessions() as s:
            for table in (SheetLink, WooLink):
                found = await s.scalar(
                    select(table.id).where(table.workspace_id == self.workspace_id)
                )
                if found is not None:
                    return True
        return False

    async def not_linked(self) -> None:
        """409 while a sheet or a store is the list: an edit here would be undone by it (D58)."""
        if await self.linked():
            raise SheetLinked()

    # Shared by every table of things taken (`models.taken`)

    async def add[T: Taken](self, table: type[T], **fields: Any) -> T:
        """Saves a new one with the business's next number."""
        async with self.sessions() as s, s.begin():
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

    async def find[T: Taken](
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

    async def here[T: Taken](self, table: type[T]) -> list[T]:
        """What was taken in this conversation, newest first (D55): the visitor needs no code
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

    async def before[T: Taken](self, table: type[T], limit: int = 1) -> list[T]:
        """What this visitor had before, newest first (D81, D114): in this conversation or an
        earlier one from the same chat on a messaging app, or the same browser on the web chat
        and its calls. A second order in the same chat is offered the first one's details too
        (D123). Only this conversation's when it has no `contact`."""
        here = await self._here()
        if here is None:
            return []
        ws = self.workspace_id
        if here.contact is None:
            same = table.conversation_id == here.id
        else:
            channels = BROWSER if here.channel in BROWSER else (here.channel,)
            same = and_(Conversation.channel.in_(channels), Conversation.contact == here.contact)
        async with self.sessions() as s:
            found = await s.scalars(
                select(table)
                .join(Conversation, table.conversation_id == Conversation.id)
                .where(table.workspace_id == ws, Conversation.workspace_id == ws, same)
                .order_by(table.created_at.desc())
                .limit(limit)
            )
            return list(found)

    # What the agent remembers about a visitor (D124): kept on their `Visitor` row

    async def remembered(self) -> dict[str, str]:
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

    async def _here(self) -> Conversation | None:
        if self.conversation_id is None:
            return None
        async with self.sessions() as s:
            found: Conversation | None = await s.scalar(
                select(Conversation).where(
                    Conversation.workspace_id == self.workspace_id,
                    Conversation.id == self.conversation_id,
                )
            )
            return found

    async def _visitor(self, s: AsyncSession) -> Visitor | None:
        """This conversation's visitor, keyed as the limits key them (D51): `web:<token>` for
        the web chat and calls, `messenger:<psid>` and so on."""
        here = await self._here()
        if here is None or here.contact is None:
            return None
        channel = "web" if here.channel in BROWSER else here.channel
        found: Visitor | None = await s.scalar(
            select(Visitor).where(
                Visitor.workspace_id == self.workspace_id,
                Visitor.key == f"{channel}:{here.contact}",
            )
        )
        return found

    async def get[T: Taken](self, table: type[T], row_id: str) -> T:
        async with self.sessions() as s:
            return await self._one(s, table, row_id)

    async def by_number[T: Taken](self, table: type[T], number: int) -> T | None:
        async with self.sessions() as s:
            found: T | None = await s.scalar(
                select(table).where(table.workspace_id == self.workspace_id, table.number == number)
            )
            return found

    async def set_status[T: Taken](self, table: type[T], row_id: str, status: str) -> T:
        async with self.sessions() as s, s.begin():
            row = await self._one(s, table, row_id)
            self._move(row, status)
        return row

    def _move(self, row: Taken, status: str) -> None:
        """`row.move`, with its problems as the errors the owner's routes return."""
        try:
            row.move(status, self.now())
        except LookupError as error:
            raise HeyDeskError(str(error)) from None
        except ValueError as error:
            raise InvalidTransition(str(error)) from None

    async def _one[T: Taken](self, s: AsyncSession, table: type[T], row_id: str) -> T:
        row: T | None = await s.scalar(
            select(table).where(table.id == row_id, table.workspace_id == self.workspace_id)
        )
        if row is None:
            raise NotFound("There's no such record.")
        return row


class Desk:
    """One kind of business. A desk subclasses this and fills in the class attributes."""

    kind: ClassVar[str]  # "clinic": saved on the agent
    label: ClassVar[str]  # "Clinic front desk"
    description: ClassVar[str]  # for the owner, in plain words
    config_model: ClassVar[type[AgentConfig]]  # its config: the shared fields and its own
    tools: ClassVar[list[Tool]]  # its own; the shared ones are added (`desks.tools_for`)
    takes_photos: ClassVar[bool] = False  # a visitor can send it a photo (the shop, D57)
    takes_voice: ClassVar[bool] = False  # a visitor can call it (the receptionist and clinic, D108)
    columns: ClassVar[list[Column]] = []  # its list's fields, for an import or a sheet (D53, D58)

    def store(
        self,
        sessions: async_sessionmaker[AsyncSession],
        workspace_id: str,
        now: Callable[[], datetime],
        *,
        conversation_id: str | None = None,
        knowledge: "KnowledgeService | None" = None,
        public_url: str = "",
        photos: "PhotoService | None" = None,
    ) -> Store:
        """Its data. A desk with tables of its own returns its own store."""
        return Store(
            sessions,
            workspace_id,
            now,
            conversation_id=conversation_id,
            knowledge=knowledge,
            public_url=public_url,
            photos=photos,
        )

    def own_tools(self, config: Any) -> list[Tool]:
        """Its tools for this business. A desk whose tools depend on a switch in its config
        (the restaurant's `takes_bookings`) adds them here."""
        return self.tools

    async def prompt(self, config: Any, store: Any) -> str:
        """Its section of the system prompt: who the agent is, how it works, what it knows."""
        raise NotImplementedError

    def note(self, config: Any, state: dict[str, Any]) -> str:
        """A short note from the conversation state, added before every model call."""
        return ""

    async def photo_line(self, store: Any, matches: list[tuple[str, float]] | None) -> str:
        """The line the model reads about a visitor's photo: what it's like, from `matches`
        (the closest product photos, best first; None when they couldn't be searched)."""
        return "[Photo.]"

    async def missing(self, config: Any, store: Any) -> list[str]:
        """What the owner still has to fill in, in their words. The agent answers meanwhile."""
        return missing_basics(config)

    def read_list(self, text: str, mapping: dict[str, str] | None) -> Sequence[BaseModel]:
        """Its list from a CSV's text, without saving it: problems per row (D50, D58)."""
        raise NotFound("This agent has no list to keep in a sheet.")

    async def seed(self, store: Any, data: dict[str, Any]) -> None:
        """The dev agent's own lists from its seed file (`menu:`, `doctors:`), popped from `data`."""


def missing_basics(config: AgentConfig) -> list[str]:
    missing = []
    if not config.address.strip():
        missing.append("Add your address.")
    if not config.hours.is_set():
        missing.append("Add your opening hours.")
    if not (config.handoff.phone or config.handoff.email):
        missing.append("Add a phone number or email for when someone needs a person.")
    return missing
