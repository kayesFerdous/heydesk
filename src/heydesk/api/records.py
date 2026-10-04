"""What the agents took: orders, messages for the team, and conversations (design §9)."""

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from heydesk.api.deps import Svc, Workspace

router = APIRouter(tags=["records"])

type OrderStatus = Literal["new", "ready", "collected", "cancelled"]


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    conversation_id: str
    number: int
    lines: list[dict[str, Any]]  # {code, name, quantity, price}
    total: int  # minor units
    currency: str
    pickup_time: datetime  # UTC
    customer_name: str
    customer_phone: str
    note: str
    status: str
    created_at: datetime


class StatusIn(BaseModel):
    status: OrderStatus


class RequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    conversation_id: str
    task: str
    details: dict[str, str]
    name: str
    phone: str
    status: str
    created_at: datetime


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
    orders: list[OrderOut]
    requests: list[RequestOut]


@router.get("/api/orders")
async def orders(services: Svc, ws: Workspace) -> list[OrderOut]:
    return [OrderOut.model_validate(o) for o in await services.orders.list(ws)]


@router.patch("/api/orders/{order_id}")
async def set_order_status(order_id: str, body: StatusIn, services: Svc, ws: Workspace) -> OrderOut:
    return OrderOut.model_validate(await services.orders.set_status(ws, order_id, body.status))


@router.get("/api/requests")
async def requests(services: Svc, ws: Workspace) -> list[RequestOut]:
    return [RequestOut.model_validate(r) for r in await services.requests.list(ws)]


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
    orders = await services.orders.list(ws, conversation_id=conv.id)
    requests = await services.requests.list(ws, conversation_id=conv.id)
    return ConversationWithMessages(
        **ConversationOut.model_validate(conv).model_dump(),
        messages=said,
        orders=[OrderOut.model_validate(o) for o in orders],
        requests=[RequestOut.model_validate(r) for r in requests],
    )
