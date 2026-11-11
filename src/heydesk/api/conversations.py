"""The team's inbox: the chats, live updates, and what the team does with one (design §9)."""

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from heydesk.api.deps import LoggedInUser, Paged, Svc, WorkspaceId
from heydesk.api.sse import live_frames
from heydesk.api.visitors import VisitorOut, visitor_out
from heydesk.models import Conversation
from heydesk.services import Services
from heydesk.services.visitors import visitor_key

router = APIRouter(tags=["conversations"])


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    channel: str
    name: str | None  # from the messaging app (Telegram, WhatsApp); None on the web
    status: str  # active / needs_human / human (the team is answering)
    assigned_to: str | None  # the team member who has it, or None
    assigned_email: str | None = None
    done_at: datetime | None  # Done: hidden until the visitor writes again
    snoozed_until: datetime | None  # hidden until then
    labels: list[str]  # the team's own words for it
    preview: str = ""  # the last thing said, cut short, for the list's row
    created_at: datetime
    updated_at: datetime


PREVIEW_CHARS = 120  # characters of the last line a list row shows


async def conversations_out(
    services: Services, workspace_id: str, conversations: list[Conversation]
) -> list[ConversationOut]:
    """The conversations with the email of whoever has each, and the last thing said."""
    emails = {u.id: u.email for u in await services.members.members(workspace_id)}
    out = []
    for c in conversations:
        said = [line.text for line in lines(c) if line.text]
        update = {
            "assigned_email": emails.get(c.assigned_to or ""),
            "preview": said[-1][:PREVIEW_CHARS] if said else "",
        }
        out.append(ConversationOut.model_validate(c).model_copy(update=update))
    return out


class Line(BaseModel):
    index: int  # its place in the whole chat, for `before` and `history_from`
    role: Literal["user", "assistant"]
    text: str  # empty for photos the agent showed
    photo: str = ""  # the photo the visitor sent with it
    photos: list[dict[str, str]] = []  # products the agent showed: {name, price, url}


def lines(conversation: Conversation) -> list[Line]:
    """What the team sees of a chat, oldest first: tool calls and results left out."""
    said = []
    for i, m in enumerate(conversation.messages):
        if m.get("photos"):  # a tool's result, kept only for the photos it showed
            said.append(Line(index=i, role="assistant", text="", photos=m["photos"]))
        elif (
            m.get("role") in ("user", "assistant")
            and isinstance(m.get("content"), str)
            and m["content"].strip()
            and not m.get("tool_calls")
        ):
            said.append(Line(index=i, role=m["role"], text=m["content"], photo=m.get("photo", "")))
    return said


class ConversationWithMessages(ConversationOut):
    messages: list[Line]  # what was said, oldest first; tool calls and results left out
    count: int = 0  # every line in the chat, however many came
    more: bool = False  # older lines are left, for `?before=` the first one's index
    history_from: int = 0  # the agent remembers the lines from this index on
    # who wrote it, for Block and AI off; None for a chat from before visitors were known
    visitor: VisitorOut | None = None


@router.get("/api/conversations/live")
async def live(services: Svc, workspace_id: WorkspaceId) -> StreamingResponse:
    """A stream of `event: changed` frames, one per chat that changed in this business.
    The inbox keeps it open and fetches that chat; a `ping` every 20 seconds keeps a proxy from
    closing it. It needs the Bearer header, so the page reads it with `fetch`, not
    `EventSource`. After a reconnect the page fetches the list again."""
    return StreamingResponse(
        live_frames(services.live, workspace_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/api/conversations")
async def conversations(
    services: Svc,
    workspace_id: WorkspaceId,
    page: Paged,
    current_user: LoggedInUser,
    status: Literal["active", "needs_human", "human"] | None = None,
    channel: Literal["web", "voice", "telegram", "messenger", "whatsapp"] | None = None,
    assigned: Literal["me", "none"] | None = None,
    q: Annotated[str, Query(max_length=100)] = "",
    done: bool = False,
    label: Annotated[str, Query(max_length=40)] = "",
) -> list[ConversationOut]:
    """Newest activity first, a page at a time (`limit`, `before` an `updated_at`),
    optionally from one channel, and only the caller's (`assigned=me`) or no one's.
    Done and snoozed chats are left out, or only the done ones with `done=true`; `q` searches
    every chat's name and what was said, such as a phone or an order code."""
    found = await services.inbox.list(
        workspace_id,
        status=status,
        channel=channel,
        assigned_to=current_user.user_id if assigned == "me" else None,
        unassigned=assigned == "none",
        q=q,
        done=done,
        label=label.strip().lower(),
        limit=page.limit,
        before=page.before,
    )
    return await conversations_out(services, workspace_id, found)


class CountsOut(BaseModel):
    needs_human: int  # shown chats waiting for a person
    mine: int  # of those, the caller's
    unassigned: int  # of those, no one's yet
    orders_new: int  # shop orders still new
    messages_new: int  # messages for the team still new


@router.get("/api/conversations/counts")
async def counts(services: Svc, workspace_id: WorkspaceId, current_user: LoggedInUser) -> CountsOut:
    """The inbox's numbers: how many chats wait for a person, the caller's and no one's,
    and the orders and messages still new."""
    return CountsOut(**await services.inbox.counts(workspace_id, current_user.user_id))


@router.get("/api/conversations/{conversation_id}")
async def conversation(
    conversation_id: str,
    services: Svc,
    workspace_id: WorkspaceId,
    before: Annotated[int | None, Query(ge=0)] = None,
    limit: Annotated[int | None, Query(ge=1, le=200)] = None,
) -> ConversationWithMessages:
    """The chat with what was said. `limit`: only the newest that many lines, before the line
    `before` if given; the screen loads older ones as the team scrolls up."""
    conversation = await services.inbox.get(workspace_id, conversation_id)
    said = lines(conversation)
    count = len(said)
    if before is not None:
        said = [line for line in said if line.index < before]
    more = limit is not None and len(said) > limit
    if limit is not None:
        said = said[-limit:]
    [head] = await conversations_out(services, workspace_id, [conversation])
    found = (
        await services.visitors.find(
            workspace_id, visitor_key(conversation.channel, conversation.contact)
        )
        if conversation.contact
        else None
    )
    visitor = visitor_out(found, services.visitors.now()) if found else None
    return ConversationWithMessages(
        **head.model_dump(),
        messages=said,
        count=count,
        more=more,
        history_from=conversation.history_from,
        visitor=visitor,
    )


class HandBackIn(BaseModel):
    status: Literal["active"]  # the only move: a person dealt with it, the agent answers again


@router.patch("/api/conversations/{conversation_id}")
async def hand_back(
    conversation_id: str, body: HandBackIn, services: Svc, workspace_id: WorkspaceId
) -> ConversationOut:
    """Marks a hand-off handled. 409 `invalid_transition` if nobody was waiting."""
    [handled] = await conversations_out(
        services,
        workspace_id,
        [await services.inbox.hand_back(workspace_id, conversation_id)],
    )
    return handled


class DoneIn(BaseModel):
    done: bool


@router.put("/api/conversations/{conversation_id}/done")
async def mark_done(
    conversation_id: str, body: DoneIn, services: Svc, workspace_id: WorkspaceId
) -> ConversationOut:
    """Done hides the chat until the visitor writes again; `false` shows it again."""
    [marked] = await conversations_out(
        services,
        workspace_id,
        [await services.inbox.mark_done(workspace_id, conversation_id, body.done)],
    )
    return marked


class SnoozeIn(BaseModel):
    until: datetime | None  # with a timezone; None: back now


@router.put("/api/conversations/{conversation_id}/snooze")
async def snooze(
    conversation_id: str, body: SnoozeIn, services: Svc, workspace_id: WorkspaceId
) -> ConversationOut:
    """Hides the chat until `until`, when it comes back and the owner gets an alert.
    A 422 for a time not in the next 30 days."""
    [snoozed] = await conversations_out(
        services,
        workspace_id,
        [await services.inbox.snooze(workspace_id, conversation_id, body.until)],
    )
    return snoozed


class LabelsIn(BaseModel):
    labels: list[Annotated[str, Field(max_length=40)]] = Field(max_length=20)


@router.put("/api/conversations/{conversation_id}/labels")
async def set_labels(
    conversation_id: str, body: LabelsIn, services: Svc, workspace_id: WorkspaceId
) -> ConversationOut:
    """The chat's labels, the whole list; `?label=` on the list filters by one."""
    conversation = await services.inbox.set_labels(workspace_id, conversation_id, body.labels)
    [labelled] = await conversations_out(services, workspace_id, [conversation])
    return labelled


class NoteIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class NoteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    author: str  # the team member's email
    text: str
    created_at: datetime


@router.get("/api/conversations/{conversation_id}/notes")
async def notes(conversation_id: str, services: Svc, workspace_id: WorkspaceId) -> list[NoteOut]:
    """The team's notes on the chat, oldest first. The visitor never sees them."""
    found = await services.inbox.notes(workspace_id, conversation_id)
    return [NoteOut.model_validate(n) for n in found]


@router.post("/api/conversations/{conversation_id}/notes", status_code=201)
async def add_note(
    conversation_id: str,
    body: NoteIn,
    services: Svc,
    workspace_id: WorkspaceId,
    current_user: LoggedInUser,
) -> NoteOut:
    """A note for the team: added, never edited; not in the chat."""
    author = next(
        (
            m.email
            for m in await services.members.members(workspace_id)
            if m.id == current_user.user_id
        ),
        "",
    )
    note = await services.inbox.add_note(workspace_id, conversation_id, author, body.text)
    return NoteOut.model_validate(note)


class ReplyIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


@router.post("/api/conversations/{conversation_id}/reply")
async def reply(
    conversation_id: str, body: ReplyIn, services: Svc, workspace_id: WorkspaceId
) -> ConversationOut:
    """The team answers from the inbox: the words reach the visitor and the chat, and
    the chat is taken over. On Messenger and WhatsApp only within 24 hours of the
    visitor's last message: Meta refuses it after, and nothing reaches them."""
    conversation = await services.inbox.team_reply(workspace_id, conversation_id, body.text)
    [replied] = await conversations_out(services, workspace_id, [conversation])
    return replied


class AssistIn(BaseModel):
    kind: Literal["summary", "ask", "draft"]
    question: str = Field(default="", max_length=500)  # for `ask`


class AssistOut(BaseModel):
    text: str  # for the team; a draft goes in the reply box, and nothing is sent


@router.post("/api/conversations/{conversation_id}/assist")
async def assist(
    conversation_id: str, body: AssistIn, services: Svc, workspace_id: WorkspaceId
) -> AssistOut:
    """The model's help on a chat: `summary`, `ask` (with `question`), or `draft` (the
    next reply, for the team to check and send). No tools, nothing added or sent; traced and
    billed as a turn. A 503 when the model didn't answer."""
    agent = await services.agents.by_workspace(workspace_id)
    text = await services.conversations.assist(agent, conversation_id, body.kind, body.question)
    return AssistOut(text=text)


class TakeOverIn(BaseModel):
    on: bool  # true: the team has the chat and the agent is quiet; false: the agent's again


@router.put("/api/conversations/{conversation_id}/takeover")
async def take_over(
    conversation_id: str, body: TakeOverIn, services: Svc, workspace_id: WorkspaceId
) -> ConversationOut:
    """The inbox's *Take over* switch, as "." in Business Suite or on Telegram: the
    agent stays quiet in the chat until it's switched off, or the team is quiet for
    TAKEOVER_QUIET_MINUTES."""
    conversation = await services.inbox.take_over(workspace_id, conversation_id, body.on)
    [taken] = await conversations_out(services, workspace_id, [conversation])
    return taken


class AssigneeIn(BaseModel):
    user_id: str | None  # a team member's id, or None: no one has it


@router.put("/api/conversations/{conversation_id}/assignee")
async def assign(
    conversation_id: str,
    body: AssigneeIn,
    services: Svc,
    workspace_id: WorkspaceId,
    current_user: LoggedInUser,
) -> ConversationOut:
    """Who has the chat. The owner gives any chat to anyone on the team, or no one; a
    moderator takes an unassigned one for themself (their own id) and gives back their own
    (`null`); anything else is a 403. 404 for someone not on the team."""
    conversation = await services.inbox.assign(
        workspace_id, conversation_id, current_user, body.user_id
    )
    [assigned] = await conversations_out(services, workspace_id, [conversation])
    return assigned
