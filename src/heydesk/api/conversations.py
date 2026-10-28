"""Conversations, and the messages for the team that every desk takes (design §9)."""

from dataclasses import asdict
from datetime import datetime, time, timedelta
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field

from heydesk.api.deps import Paged, StatusIn, Svc, User, Workspace
from heydesk.models import Conversation, Lead, TeamMessage, Ticket, Visitor
from heydesk.services import Services
from heydesk.services.visitors import is_blocked, key_of

router = APIRouter(tags=["conversations"])


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    channel: str
    name: str | None  # from the messaging app (Telegram, WhatsApp); None on the web
    status: str  # active / needs_human / human (the team is answering, D43)
    assigned_to: str | None  # the team member who has it, or None (I1)
    assigned_email: str | None = None
    done_at: datetime | None  # Done: hidden until the visitor writes again (I3)
    snoozed_until: datetime | None  # hidden until then (I3)
    labels: list[str]  # the team's own words for it (D136)
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
    index: int  # its place in the whole chat, for `before` and `history_from` (D157)
    role: Literal["user", "assistant"]
    text: str  # empty for photos the agent showed
    photo: str = ""  # the photo the visitor sent with it (D57)
    photos: list[dict[str, str]] = []  # products the agent showed: {name, price, url} (D57)


class VisitorOut(BaseModel):
    """What the team decided about one visitor (I2)."""

    id: str
    blocked: bool
    blocked_why: str | None  # "team", "flooding" or "off_topic"
    blocked_until: datetime | None  # None while blocked: until someone unblocks them
    blocked_by: str | None  # the email of who pressed Block
    ai_off: bool  # the team answers them by hand


def visitor_out(visitor: Visitor, now: datetime) -> VisitorOut:
    blocked = is_blocked(visitor, now)
    return VisitorOut(
        id=visitor.id,
        blocked=blocked,
        blocked_why=visitor.blocked_why if blocked else None,
        blocked_until=visitor.blocked_until if blocked else None,
        blocked_by=visitor.blocked_by if blocked else None,
        ai_off=visitor.ai_off,
    )


class ConversationWithMessages(ConversationOut):
    messages: list[Line]  # what was said, oldest first; tool calls and results left out
    count: int = 0  # every line in the chat, however many came (D157)
    more: bool = False  # older lines are left, for `?before=` the first one's index (D157)
    history_from: int = 0  # the agent remembers the lines from this index on (D157)
    # who wrote it, for Block and AI off (I2); None for a chat from before visitors were known
    visitor: VisitorOut | None = None


class BlockedOut(VisitorOut):
    conversation_id: str | None  # their latest chat
    channel: str | None
    name: str | None


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
    q: Annotated[str, Query(max_length=100)] = "",
    done: bool = False,
    label: Annotated[str, Query(max_length=40)] = "",
) -> list[ConversationOut]:
    """Newest activity first, a page at a time (`limit`, `before` an `updated_at`),
    optionally from one channel, and only the caller's (`assigned=me`) or no one's (I1).
    Done and snoozed chats are left out, or only the done ones with `done=true`; `q` searches
    every chat's name and what was said, such as a phone or an order code (I3)."""
    found = await services.conversations.list(
        ws,
        status=status,
        channel=channel,
        assigned_to=principal.user_id if assigned == "me" else None,
        unassigned=assigned == "none",
        q=q,
        done=done,
        label=label.strip().lower(),
        limit=page.limit,
        before=page.before,
    )
    return await out(services, ws, found)


class CountsOut(BaseModel):
    needs_human: int  # shown chats waiting for a person
    mine: int  # of those, the caller's
    unassigned: int  # of those, no one's yet


@router.get("/api/conversations/counts")
async def counts(services: Svc, ws: Workspace, principal: User) -> CountsOut:
    """The inbox's numbers: how many chats wait for a person, the caller's and no one's (I1)."""
    return CountsOut(**await services.conversations.counts(ws, principal.user_id))


class StatsOut(BaseModel):
    conversations: int  # chats the agent answered
    handed_off: int  # of those, handed to a person; the rest the agent handled alone
    sales: int  # orders or bookings, not cancelled or waiting for payment
    amount: int  # their total, minor units
    bought: int  # chats that ended in one
    sources: dict[str, int]  # chat, page or team
    leads: int  # call backs asked for and not sold


class DashboardOut(BaseModel):
    days: int
    currency: str
    today: StatsOut
    yesterday: StatsOut
    period: StatsOut  # the last `days` days, today included


@router.get("/api/stats")
async def stats(
    services: Svc, ws: Workspace, days: Annotated[int, Query(ge=1, le=90)] = 7
) -> DashboardOut:
    """The owner's dashboard (D144): today, yesterday and the last `days` days, in the
    business's own days."""
    agent = await services.agents.by_workspace(ws)
    store = await services.agents.store(ws)
    zone = ZoneInfo(agent.config.timezone)
    today = datetime.combine(store.now().astimezone(zone).date(), time(), zone)
    day = timedelta(days=1)

    async def between(start: datetime, end: datetime) -> StatsOut:
        return StatsOut(**asdict(await store.stats(start, end)))

    return DashboardOut(
        days=days,
        currency=agent.config.currency,
        today=await between(today, today + day),
        yesterday=await between(today - day, today),
        period=await between(today - (days - 1) * day, today + day),
    )


@router.get("/api/conversations/{conversation_id}")
async def conversation(
    conversation_id: str,
    services: Svc,
    ws: Workspace,
    before: Annotated[int | None, Query(ge=0)] = None,
    limit: Annotated[int | None, Query(ge=1, le=200)] = None,
) -> ConversationWithMessages:
    """The chat with what was said. `limit`: only the newest that many lines, before the line
    `before` if given; the screen loads older ones as the team scrolls up (D157)."""
    conv = await services.conversations.get(ws, conversation_id)
    said = []
    for i, m in enumerate(conv.messages):
        if m.get("photos"):  # a tool's result, kept only for the photos it showed
            said.append(Line(index=i, role="assistant", text="", photos=m["photos"]))
        elif (
            m.get("role") in ("user", "assistant")
            and isinstance(m.get("content"), str)
            and m["content"].strip()
            and not m.get("tool_calls")
        ):
            line = Line(index=i, role=m["role"], text=m["content"], photo=m.get("photo", ""))
            said.append(line)
    count = len(said)
    if before is not None:
        said = [line for line in said if line.index < before]
    more = limit is not None and len(said) > limit
    if limit is not None:
        said = said[-limit:]
    [head] = await out(services, ws, [conv])
    found = (
        await services.visitors.find(ws, key_of(conv.channel, conv.contact))
        if conv.contact
        else None
    )
    visitor = visitor_out(found, services.visitors.now()) if found else None
    return ConversationWithMessages(
        **head.model_dump(),
        messages=said,
        count=count,
        more=more,
        history_from=conv.history_from,
        visitor=visitor,
    )


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


class DoneIn(BaseModel):
    done: bool


@router.put("/api/conversations/{conversation_id}/done")
async def mark_done(
    conversation_id: str, body: DoneIn, services: Svc, ws: Workspace
) -> ConversationOut:
    """Done hides the chat until the visitor writes again; `false` shows it again (I3)."""
    [marked] = await out(
        services, ws, [await services.conversations.mark_done(ws, conversation_id, body.done)]
    )
    return marked


class SnoozeIn(BaseModel):
    until: datetime | None  # with a timezone; None: back now


@router.put("/api/conversations/{conversation_id}/snooze")
async def snooze(
    conversation_id: str, body: SnoozeIn, services: Svc, ws: Workspace
) -> ConversationOut:
    """Hides the chat until `until`, when it comes back and the owner gets an alert (I3).
    A 422 for a time not in the next 30 days."""
    [snoozed] = await out(
        services, ws, [await services.conversations.snooze(ws, conversation_id, body.until)]
    )
    return snoozed


class LabelsIn(BaseModel):
    labels: list[Annotated[str, Field(max_length=40)]] = Field(max_length=20)


@router.put("/api/conversations/{conversation_id}/labels")
async def set_labels(
    conversation_id: str, body: LabelsIn, services: Svc, ws: Workspace
) -> ConversationOut:
    """The chat's labels, the whole list (D136); `?label=` on the list filters by one."""
    conv = await services.conversations.set_labels(ws, conversation_id, body.labels)
    [labelled] = await out(services, ws, [conv])
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
async def notes(conversation_id: str, services: Svc, ws: Workspace) -> list[NoteOut]:
    """The team's notes on the chat, oldest first (D136). The visitor never sees them."""
    found = await services.conversations.notes(ws, conversation_id)
    return [NoteOut.model_validate(n) for n in found]


@router.post("/api/conversations/{conversation_id}/notes", status_code=201)
async def add_note(
    conversation_id: str, body: NoteIn, services: Svc, ws: Workspace, principal: User
) -> NoteOut:
    """A note for the team (D136): added, never edited; not in the chat."""
    author = next(
        (m.email for m in await services.members.members(ws) if m.id == principal.user_id), ""
    )
    note = await services.conversations.add_note(ws, conversation_id, author, body.text)
    return NoteOut.model_validate(note)


class ReplyIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


@router.post("/api/conversations/{conversation_id}/reply")
async def reply(
    conversation_id: str, body: ReplyIn, services: Svc, ws: Workspace
) -> ConversationOut:
    """The team answers from the inbox (D136): the words reach the visitor and the chat, and
    the chat is taken over (D135). On Messenger and WhatsApp only within 24 hours of the
    visitor's last message: Meta refuses it after, and nothing reaches them."""
    conv = await services.conversations.team_reply(ws, conversation_id, body.text)
    [replied] = await out(services, ws, [conv])
    return replied


class AssistIn(BaseModel):
    kind: Literal["summary", "ask", "draft"]
    question: str = Field(default="", max_length=500)  # for `ask`


class AssistOut(BaseModel):
    text: str  # for the team; a draft goes in the reply box, and nothing is sent


@router.post("/api/conversations/{conversation_id}/assist")
async def assist(conversation_id: str, body: AssistIn, services: Svc, ws: Workspace) -> AssistOut:
    """The model's help on a chat (D137): `summary`, `ask` (with `question`), or `draft` (the
    next reply, for the team to check and send). No tools, nothing added or sent; traced and
    billed as a turn. A 503 when the model didn't answer."""
    agent = await services.agents.by_workspace(ws)
    text = await services.conversations.assist(agent, conversation_id, body.kind, body.question)
    return AssistOut(text=text)


class TakeOverIn(BaseModel):
    on: bool  # true: the team has the chat and the agent is quiet; false: the agent's again


@router.put("/api/conversations/{conversation_id}/takeover")
async def take_over(
    conversation_id: str, body: TakeOverIn, services: Svc, ws: Workspace
) -> ConversationOut:
    """The inbox's *Take over* switch (D135), as "." in Business Suite or on Telegram: the
    agent stays quiet in the chat until it's switched off, or the team is quiet for
    TAKEOVER_QUIET_MINUTES."""
    conv = await services.conversations.take_over(ws, conversation_id, body.on)
    [taken] = await out(services, ws, [conv])
    return taken


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


class VisitorIn(BaseModel):
    blocked: bool | None = None  # true: no reply at all, until false
    ai_off: bool | None = None  # true: their messages go to the team, with no AI turn


@router.patch("/api/visitors/{visitor_id}")
async def change_visitor(
    visitor_id: str, body: VisitorIn, services: Svc, ws: Workspace, principal: User
) -> VisitorOut:
    """Block or unblock a visitor, or turn the AI off or on for them (I2); the owner or a
    moderator. Their messages run no turn and cost nothing either way."""
    emails = {u.id: u.email for u in await services.members.members(ws)}
    by = emails.get(principal.user_id, "HeyDesk")  # an admin isn't on the team
    visitor = await services.visitors.change(ws, visitor_id, by, body.blocked, body.ai_off)
    return visitor_out(visitor, services.visitors.now())


@router.get("/api/visitors/blocked")
async def blocked(services: Svc, ws: Workspace) -> list[BlockedOut]:
    """Who is blocked now, by the team, for flooding or for chatting off topic, with their
    latest chat; the newest block first."""
    now = services.visitors.now()
    found = []
    for v in await services.visitors.blocked(ws):
        conv = await services.conversations.of_visitor(ws, v.key)
        found.append(
            BlockedOut(
                **visitor_out(v, now).model_dump(),
                conversation_id=conv.id if conv else None,
                channel=conv.channel if conv else None,
                name=conv.name if conv else None,
            )
        )
    return found


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


class TicketOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    code: str  # "C-101"
    conversation_id: str | None
    status: str  # open / solved
    reason: str  # the agent's words for what's wrong
    assigned_email: str | None = None  # who has its chat (I1)
    created_at: datetime  # how old it is
    updated_at: datetime


@router.get("/api/tickets")
async def tickets(
    services: Svc, ws: Workspace, page: Paged, status: Literal["open", "solved"] | None = None
) -> list[TicketOut]:
    """Complaints the agent flagged (D138), newest first, a page at a time; `status=open`
    for the ones still to deal with. Each with who has its chat."""
    store = await services.agents.store(ws)
    found = await store.find(Ticket, status, limit=page.limit, before=page.before)
    chats = {
        c.id: c
        for c in await services.conversations.by_ids(
            ws, {t.conversation_id for t in found if t.conversation_id}
        )
    }
    emails = {u.id: u.email for u in await services.members.members(ws)}
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
    ticket_id: str, body: StatusIn, services: Svc, ws: Workspace
) -> TicketOut:
    """`solved`, or `open` again. It stays open until someone marks it solved (D138)."""
    store = await services.agents.store(ws)
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
    ws: Workspace,
    page: Paged,
    status: Literal["open", "sold", "not_now", "lost"] | None = None,
) -> list[LeadOut]:
    """Call backs the agent took (D139), newest first; `status=open` for the ones to call."""
    store = await services.agents.store(ws)
    found = await store.find(Lead, status, limit=page.limit, before=page.before)
    return [LeadOut.model_validate(lead) for lead in found]


@router.get("/api/leads/why")
async def lost_why(services: Svc, ws: Workspace) -> list[WhyOut]:
    """Why we lose sales: the lost leads' whys, counted, most first (D139)."""
    store = await services.agents.store(ws)
    return [WhyOut(why=why, count=n) for why, n in await store.lost_why()]


@router.patch("/api/leads/{lead_id}")
async def lead_outcome(lead_id: str, body: LeadIn, services: Svc, ws: Workspace) -> LeadOut:
    """The team taps how it went: sold, not now, or lost with why; `open` undoes it."""
    store = await services.agents.store(ws)
    return LeadOut.model_validate(await store.lead_outcome(lead_id, body.status, body.why))


@router.post("/api/conversations/{conversation_id}/tester")
async def add_tester(conversation_id: str, services: Svc, ws: Workspace) -> dict[str, list[str]]:
    """*This is me*: the chat's visitor is one of the owner's test accounts, answered while the
    agent practises (D143). Says who the testers are now."""
    conv = await services.conversations.get(ws, conversation_id)
    agent = await services.agents.by_workspace(ws)
    key = f"{conv.channel}:{conv.contact}"
    testers = list(dict.fromkeys([*agent.config.testers, key]))
    await services.agents.update(ws, {"testers": testers})
    if conv.status != "active":  # its practice messages waited for the team: the agent's now
        await services.conversations.hand_back(ws, conversation_id)
    return {"testers": testers}
