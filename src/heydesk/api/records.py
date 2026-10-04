"""What the agent took (orders and messages, one inbox) and conversations (design §9)."""

from dataclasses import asdict
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from heydesk.api.deps import Svc, Workspace

router = APIRouter(tags=["records"])


class RecordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    conversation_id: str
    job: str  # which task made it: "order", "message"
    kind: Literal["order", "request"]
    number: int  # what the visitor was told
    status: str
    customer_name: str
    customer_phone: str
    details: dict[str, Any]  # a request's: label → value
    note: str
    # An order's; empty for a request
    lines: list[dict[str, Any]] | None  # {code, name, quantity, price}
    total: int | None  # minor units
    currency: str | None
    due_at: datetime | None  # the pickup time, UTC
    created_at: datetime
    updated_at: datetime


class StatusIn(BaseModel):
    status: str  # where it may go depends on its kind: see the 409 message


class InboxOut(BaseModel):
    job: str
    title: str
    kind: Literal["order", "request"]
    waiting: int  # new orders or messages
    total: int


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    channel: str
    status: str  # active / needs_human
    created_at: datetime
    updated_at: datetime


class Line(BaseModel):
    role: Literal["user", "assistant"]
    text: str


class ConversationWithMessages(ConversationOut):
    messages: list[Line]  # what was said; tool calls and results left out
    records: list[RecordOut]


@router.get("/api/inbox")
async def inbox(services: Svc, ws: Workspace) -> list[InboxOut]:
    """The sidebar: each job the agent does, with how many are waiting."""
    tasks = await services.agents.tasks(ws)
    return [InboxOut.model_validate(asdict(e)) for e in await services.records.inbox(ws, tasks)]


@router.get("/api/records")
async def records(
    services: Svc,
    ws: Workspace,
    job: str | None = None,
    status: str | None = None,
    q: str | None = None,
) -> list[RecordOut]:
    """Orders and messages, newest first. `q` matches the number, name or phone."""
    found = await services.records.find(ws, job=job, status=status, q=q)
    return [RecordOut.model_validate(r) for r in found]


@router.get("/api/records/{record_id}")
async def record(record_id: str, services: Svc, ws: Workspace) -> RecordOut:
    return RecordOut.model_validate(await services.records.get(ws, record_id))


@router.patch("/api/records/{record_id}")
async def set_status(record_id: str, body: StatusIn, services: Svc, ws: Workspace) -> RecordOut:
    return RecordOut.model_validate(await services.records.set_status(ws, record_id, body.status))


@router.get("/api/conversations")
async def conversations(
    services: Svc,
    ws: Workspace,
    status: Literal["active", "needs_human"] | None = None,
) -> list[ConversationOut]:
    found = await services.conversations.list(ws, status=status)
    return [ConversationOut.model_validate(c) for c in found]


@router.get("/api/conversations/{conversation_id}")
async def conversation(
    conversation_id: str, services: Svc, ws: Workspace
) -> ConversationWithMessages:
    conv = await services.conversations.get(ws, conversation_id)
    said = [
        Line(role=m["role"], text=m["content"])
        for m in conv.messages
        if m.get("role") in ("user", "assistant")
        and isinstance(m.get("content"), str)
        and m["content"].strip()
        and not m.get("tool_calls")
    ]
    made = await services.records.find(ws, conversation_id=conv.id)
    return ConversationWithMessages(
        **ConversationOut.model_validate(conv).model_dump(),
        messages=said,
        records=[RecordOut.model_validate(r) for r in made],
    )
