"""What the agent took for a business: an order, an appointment, a message. Each kind has its
own table; they share these columns, a number per business, and a status that moves along
the table's `FLOW`."""

from datetime import datetime
from typing import TYPE_CHECKING, Any, ClassVar

from sqlalchemy import ForeignKey, String, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from heydesk.db import new_id, utcnow
from heydesk.domain.values import record_code

FIRST_NUMBER = 101  # a business's first order; "order 1" sounds like a test
FIRST_STATUS = "new"  # what's waiting for the business


class Taken:
    """A mixin for the tables. A subclass sets `LETTER` ("A" for A-104) and `FLOW`."""

    LETTER: ClassVar[str]
    FLOW: ClassVar[dict[str, set[str]]]  # status → where it may go next

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    conversation_id: Mapped[str | None] = mapped_column(ForeignKey("conversations.id"), index=True)
    number: Mapped[int]  # what the visitor is told: 101, 102… per business
    status: Mapped[str] = mapped_column(String(20), default=FIRST_STATUS)
    name: Mapped[str] = mapped_column(String(100))  # the visitor's
    phone: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)

    if TYPE_CHECKING:  # the tables that mix this in take their columns as keywords

        def __init__(self, **fields: Any) -> None: ...

    @property
    def code(self) -> str:
        """What the visitor is told and the business sees: "A-104"."""
        return record_code(self.LETTER, self.number)

    def move(self, status: str, now: datetime) -> None:
        """One status change. Raises LookupError for an unknown status, ValueError for a
        change the flow doesn't allow."""
        if status not in self.FLOW:
            raise LookupError(f"status is one of: {', '.join(self.FLOW)}")
        if status not in self.FLOW[self.status]:
            raise ValueError(f"something that is {self.status} can't become {status}")
        self.status = status
        self.updated_at = now


async def next_number(s: AsyncSession, table: type[Taken], workspace_id: str) -> int:
    """The business's next number in this table: 101, 102…"""
    last = await s.scalar(select(func.max(table.number)).where(table.workspace_id == workspace_id))
    return last + 1 if last else FIRST_NUMBER
