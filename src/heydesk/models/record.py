from datetime import datetime
from typing import Any

from sqlalchemy import JSON, CheckConstraint, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from heydesk.db import Base, new_id, utcnow
from heydesk.domain.values import record_code

# Two kinds, because there are two guarantees: an order's prices and total, a request's
# confirmed details. Every job a business offers (a task in its preset) is one of them.
KINDS = ("order", "request")
FIRST_STATUS = "new"  # what's waiting for the business

# Where each status may go next, per kind. Changed only through `Record.move`.
FLOWS: dict[str, dict[str, set[str]]] = {
    "order": {
        "new": {"ready", "cancelled"},
        "ready": {"collected", "cancelled"},
        "collected": set(),
        "cancelled": set(),
    },
    "request": {
        "new": {"handled", "archived", "cancelled"},
        "handled": {"archived", "new", "cancelled"},
        "archived": {"new"},
        "cancelled": set(),
    },
}


class Record(Base):
    """What a finished job left for the business (D24): an order or a message for the team,
    in one table, so the inbox is one list filtered by `job`. Saved only through
    `RecordService`, and only with a `Confirmed[...]` (rule 2).

    An order's columns (`lines`, `total`, `currency`, `due_at`) are null for a request.
    Prices are copied into an order, so a later menu edit doesn't change it.
    """

    __tablename__ = "records"
    __table_args__ = (
        UniqueConstraint("workspace_id", "kind", "number"),
        CheckConstraint("kind IN ('order', 'request')", name="kind"),
        CheckConstraint(
            "kind != 'order' OR (lines IS NOT NULL AND total IS NOT NULL AND due_at IS NOT NULL)",
            name="order_has_total",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), index=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    job: Mapped[str] = mapped_column(String(50))  # the task: "order", "message"
    kind: Mapped[str] = mapped_column(String(10))
    number: Mapped[int]  # what the visitor is told: 101, 102… per business and kind
    status: Mapped[str] = mapped_column(String(20), default=FIRST_STATUS)
    customer_name: Mapped[str] = mapped_column(String(100))
    customer_phone: Mapped[str] = mapped_column(String(20))
    details: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict
    )  # a request's, label → value
    note: Mapped[str] = mapped_column(default="")
    # An order's
    lines: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSON
    )  # {code, name, quantity, price}
    total: Mapped[int | None]  # minor units
    currency: Mapped[str | None] = mapped_column(String(3))
    due_at: Mapped[datetime | None]  # the pickup time, UTC
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)

    @property
    def code(self) -> str:
        """What the visitor is told and the business sees: "A-104" (D38)."""
        return record_code(self.job, self.number)

    def move(self, status: str, now: datetime) -> None:
        """One status change. Raises LookupError for an unknown status, ValueError for a
        change this kind of record doesn't allow."""
        flow = FLOWS[self.kind]
        if status not in flow:
            raise LookupError(f"status is one of: {', '.join(flow)}")
        if status not in flow[self.status]:
            raise ValueError(f"a {self.kind} that is {self.status} can't become {status}")
        self.status = status
        self.updated_at = now
