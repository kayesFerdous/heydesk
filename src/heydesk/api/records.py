"""What the agent took for the team: messages, complaints (tickets) and call backs (leads)."""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from heydesk.api.deps import Paged, StatusIn, Svc, WorkspaceId
from heydesk.models import Lead, TeamMessage, Ticket

router = APIRouter(tags=["records"])


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    conversation_id: str | None
    code: str  # what the visitor was told: "M-104"
    status: str  # new / handled
    name: str
    phone: str
    body: str
    best_time: str
    created_at: datetime
    updated_at: datetime


@router.get("/api/messages")
async def messages(
    services: Svc,
    workspace_id: WorkspaceId,
    page: Paged,
    status: str | None = None,
    q: str | None = None,
) -> list[MessageOut]:
    """Messages for the team, newest first, a page at a time (`limit`, `before`). `q`
    matches the code, the name or the phone."""
    store = await services.agents.store(workspace_id)
    found = await store.messages(status, q, limit=page.limit, before=page.before)
    return [MessageOut.model_validate(m) for m in found]


@router.get("/api/messages/{message_id}")
async def message(message_id: str, services: Svc, workspace_id: WorkspaceId) -> MessageOut:
    store = await services.agents.store(workspace_id)
    return MessageOut.model_validate(await store.get(TeamMessage, message_id))


@router.patch("/api/messages/{message_id}")
async def set_status(
    message_id: str, body: StatusIn, services: Svc, workspace_id: WorkspaceId
) -> MessageOut:
    store = await services.agents.store(workspace_id)
    return MessageOut.model_validate(await store.set_status(TeamMessage, message_id, body.status))


class TicketOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    code: str  # "C-101"
    conversation_id: str | None
    status: str  # open / solved
    reason: str  # the agent's words for what's wrong
    assigned_email: str | None = None  # who has its chat
    created_at: datetime  # how old it is
    updated_at: datetime


@router.get("/api/tickets")
async def tickets(
    services: Svc,
    workspace_id: WorkspaceId,
    page: Paged,
    status: Literal["open", "solved"] | None = None,
) -> list[TicketOut]:
    """Complaints the agent flagged, newest first, a page at a time; `status=open`
    for the ones still to deal with. Each with who has its chat."""
    store = await services.agents.store(workspace_id)
    found = await store.find(Ticket, status, limit=page.limit, before=page.before)
    chats = {
        c.id: c
        for c in await services.inbox.by_ids(
            workspace_id, {t.conversation_id for t in found if t.conversation_id}
        )
    }
    emails = {u.id: u.email for u in await services.members.members(workspace_id)}
    return [
        TicketOut.model_validate(t).model_copy(
            update={
                "assigned_email": emails.get(
                    getattr(chats.get(t.conversation_id or ""), "assigned_to", None) or ""
                )
            }
        )
        for t in found
    ]


@router.patch("/api/tickets/{ticket_id}")
async def set_ticket_status(
    ticket_id: str, body: StatusIn, services: Svc, workspace_id: WorkspaceId
) -> TicketOut:
    """`solved`, or `open` again. It stays open until someone marks it solved."""
    store = await services.agents.store(workspace_id)
    return TicketOut.model_validate(await store.set_status(Ticket, ticket_id, body.status))


class LeadOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    code: str  # "L-101"
    conversation_id: str | None
    status: str  # open / sold / not_now / lost
    name: str
    phone: str
    call_at: datetime  # when to call them
    note: str  # what they want
    why: str  # a lost one's reason
    created_at: datetime


class LeadIn(BaseModel):
    status: Literal["open", "sold", "not_now", "lost"]
    why: str = Field(default="", max_length=200)  # for lost: "price", "delivery charge"


class WhyOut(BaseModel):
    why: str
    count: int


@router.get("/api/leads")
async def leads(
    services: Svc,
    workspace_id: WorkspaceId,
    page: Paged,
    status: Literal["open", "sold", "not_now", "lost"] | None = None,
) -> list[LeadOut]:
    """Call backs the agent took, newest first; `status=open` for the ones to call."""
    store = await services.agents.store(workspace_id)
    found = await store.find(Lead, status, limit=page.limit, before=page.before)
    return [LeadOut.model_validate(lead) for lead in found]


@router.get("/api/leads/why")
async def lost_reasons(services: Svc, workspace_id: WorkspaceId) -> list[WhyOut]:
    """Why we lose sales: the lost leads' whys, counted, most first."""
    store = await services.agents.store(workspace_id)
    return [WhyOut(why=why, count=n) for why, n in await store.lost_reasons()]


@router.patch("/api/leads/{lead_id}")
async def lead_outcome(
    lead_id: str, body: LeadIn, services: Svc, workspace_id: WorkspaceId
) -> LeadOut:
    """The team taps how it went: sold, not now, or lost with why; `open` undoes it."""
    store = await services.agents.store(workspace_id)
    return LeadOut.model_validate(await store.lead_outcome(lead_id, body.status, body.why))
