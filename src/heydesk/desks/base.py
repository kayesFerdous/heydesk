"""What a desk is: one kind of business (a clinic, a restaurant), written as plain Python.

A desk owns everything that makes that business different: its config fields, its prompt
(who the agent is and how it thinks), its tools, its tables and its owner routes. What every
desk shares lives here (`Store`: messages for the team, the documents search) and in
`desks/shared.py` (the shared tools).
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, ClassVar

from sqlalchemy import String, cast, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from heydesk.domain.values import Confirmed, parse_code
from heydesk.errors import HeyDeskError, InvalidTransition, NotFound
from heydesk.models import TeamMessage
from heydesk.models.taken import Taken, next_number
from heydesk.ports.knowledge_store import Snippet
from heydesk.runtime.config import AgentConfig
from heydesk.runtime.tool import Tool

if TYPE_CHECKING:  # only a type here: services import the desks
    from heydesk.services.knowledge import KnowledgeService

PAGE = 100  # the most rows a list returns at once (newest first)


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
    ) -> None:
        self.sessions = sessions
        self.workspace_id = workspace_id
        self.now = now
        self.conversation_id = conversation_id
        self.knowledge = knowledge
        self.public_url = public_url  # where a link in a reply points: the payment page

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
        conversation_id: str | None = None,
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
        if conversation_id:
            query = query.where(table.conversation_id == conversation_id)
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
            try:
                row.move(status, self.now())
            except LookupError as error:
                raise HeyDeskError(str(error)) from None
            except ValueError as error:
                raise InvalidTransition(str(error)) from None
        return row

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

    def store(
        self,
        sessions: async_sessionmaker[AsyncSession],
        workspace_id: str,
        now: Callable[[], datetime],
        *,
        conversation_id: str | None = None,
        knowledge: "KnowledgeService | None" = None,
        public_url: str = "",
    ) -> Store:
        """Its data. A desk with tables of its own returns its own store."""
        return Store(
            sessions,
            workspace_id,
            now,
            conversation_id=conversation_id,
            knowledge=knowledge,
            public_url=public_url,
        )

    async def prompt(self, config: Any, store: Any) -> str:
        """Its section of the system prompt: who the agent is, how it works, what it knows."""
        raise NotImplementedError

    def note(self, config: Any, state: dict[str, Any]) -> str:
        """A short note from the conversation state, added before every model call."""
        return ""

    async def missing(self, config: Any, store: Any) -> list[str]:
        """What the owner still has to fill in, in their words. The agent answers meanwhile."""
        return missing_basics(config)

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
