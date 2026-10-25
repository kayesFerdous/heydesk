"""Conversations, and the messages for the team that every desk takes (design §9)."""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from heydesk.api.deps import Paged, StatusIn, Svc, User, Workspace
from heydesk.models import Conversation, TeamMessage
from heydesk.services import Services

router = APIRouter(tags=["conversations"])


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    channel: str
    name: str | None  # from the messaging app (Telegram, WhatsApp); None on the web
    status: str  # active / needs_human / human (the team is answering, D43)
    assigned_to: str | None  # the team member who has it, or None (I1)
    assigned_email: str | None = None
    created_at: datetime
    updated_at: datetime


async def out(services: Services, ws: str, convs: list[Conversation]) -> list[ConversationOut]:
    """The conversations with the email of whoever has each."""
    emails = {u.id: u.email for u in await services.members.members(ws)}
    return [
        ConversationOut.model_validate(c).model_copy(
            update={"assigned_email": emails.get(c.assigned_to or "")}
        )
        for c in convs
    ]


class Line(BaseModel):
    role: Literal["user", "assistant"]
    text: str  # empty for photos the agent showed
    photo: str = ""  # the photo the visitor sent with it (D57)
    photos: list[dict[str, str]] = []  # products the agent showed: {name, price, url} (D57)


class ConversationWithMessages(ConversationOut):
    messages: list[Line]  # what was said; tool calls and results left out


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


@router.get("/api/conversations")
async def conversations(
    services: Svc,
    ws: Workspace,
    page: Paged,
    principal: User,
    status: Literal["active", "needs_human", "human"] | None = None,
    channel: Literal["web", "voice", "telegram", "messenger", "whatsapp"] | None = None,
    assigned: Literal["me", "none"] | None = None,
) -> list[ConversationOut]:
    """Newest activity first, a page at a time (`limit`, `before` an `updated_at`),
    optionally from one channel, and only the caller's (`assigned=me`) or no one's (I1)."""
    found = await services.conversations.list(
        ws,
        status=status,
        channel=channel,
        assigned_to=principal.user_id if assigned == "me" else None,
        unassigned=assigned == "none",
        limit=page.limit,
        before=page.before,
    )
    return await out(services, ws, found)


@router.get("/api/conversations/{conversation_id}")
async def conversation(
    conversation_id: str, services: Svc, ws: Workspace
) -> ConversationWithMessages:
    conv = await services.conversations.get(ws, conversation_id)
    said = []
    for m in conv.messages:
        if m.get("photos"):  # a tool's result, kept only for the photos it showed
            said.append(Line(role="assistant", text="", photos=m["photos"]))
        elif (
            m.get("role") in ("user", "assistant")
            and isinstance(m.get("content"), str)
            and m["content"].strip()
            and not m.get("tool_calls")
        ):
            said.append(Line(role=m["role"], text=m["content"], photo=m.get("photo", "")))
    [head] = await out(services, ws, [conv])
    return ConversationWithMessages(**head.model_dump(), messages=said)


class HandBackIn(BaseModel):
    status: Literal["active"]  # the only move: a person dealt with it, the agent answers again


@router.patch("/api/conversations/{conversation_id}")
async def hand_back(
    conversation_id: str, body: HandBackIn, services: Svc, ws: Workspace
) -> ConversationOut:
    """Marks a hand-off handled (D83). 409 `invalid_transition` if nobody was waiting."""
    [handled] = await out(
        services, ws, [await services.conversations.hand_back(ws, conversation_id)]
    )
    return handled


class AssigneeIn(BaseModel):
    user_id: str | None  # a team member's id, or None: no one has it


@router.put("/api/conversations/{conversation_id}/assignee")
async def assign(
    conversation_id: str, body: AssigneeIn, services: Svc, ws: Workspace, principal: User
) -> ConversationOut:
    """Who has the chat (I1). The owner gives any chat to anyone on the team, or no one; a
    moderator takes an unassigned one for themself (their own id) and gives back their own
    (`null`); anything else is a 403. 404 for someone not on the team."""
    conv = await services.conversations.assign(ws, conversation_id, principal, body.user_id)
    [assigned] = await out(services, ws, [conv])
    return assigned


@router.get("/api/messages")
async def messages(
    services: Svc, ws: Workspace, page: Paged, status: str | None = None, q: str | None = None
) -> list[MessageOut]:
    """Messages for the team, newest first, a page at a time (`limit`, `before`). `q`
    matches the code, the name or the phone."""
    store = await services.agents.store(ws)
    found = await store.messages(status, q, limit=page.limit, before=page.before)
    return [MessageOut.model_validate(m) for m in found]


@router.get("/api/messages/{message_id}")
async def message(message_id: str, services: Svc, ws: Workspace) -> MessageOut:
    store = await services.agents.store(ws)
    return MessageOut.model_validate(await store.get(TeamMessage, message_id))


@router.patch("/api/messages/{message_id}")
async def set_status(message_id: str, body: StatusIn, services: Svc, ws: Workspace) -> MessageOut:
    store = await services.agents.store(ws)
    return MessageOut.model_validate(await store.set_status(TeamMessage, message_id, body.status))
