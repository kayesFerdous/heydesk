"""Conversations: the one entry point for web and voice (design §6)."""

import asyncio
import json
import logging
from collections.abc import AsyncGenerator, Callable, Sequence
from contextlib import aclosing, suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import ColumnElement, String, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from heydesk.config import Settings
from heydesk.desks import tools_for
from heydesk.desks.base import Store
from heydesk.errors import (
    AssistUnavailable,
    ConversationFull,
    Forbidden,
    HeyDeskError,
    InvalidConfig,
    InvalidTransition,
    NotFound,
    UnreadablePhoto,
)
from heydesk.models import Conversation, Note, Turn, User
from heydesk.runtime.events import Card, Event, HandOff, Photos, Token, TurnEnded
from heydesk.runtime.loop import Runtime, TurnInput, run_turn
from heydesk.runtime.prompt import build_system_prompt
from heydesk.services.agents import LiveAgent
from heydesk.services.calls import CallService
from heydesk.services.knowledge import KnowledgeService
from heydesk.services.photos import PhotoService
from heydesk.services.team import HUMAN, TEAM, TeamService, handoff_alert
from heydesk.services.users import Principal
from heydesk.services.visitors import VisitorService
from heydesk.services.woo import WooService

log = logging.getLogger(__name__)

# What the team asks the model about a chat (D137): the last message of a turn with no tools.
# A question is the team's words, put in with str.replace, never str.format
ASSIST = {
    "summary": (
        "[For the team, not the customer] Sum up this chat in 2 to 4 short lines: what the "
        "customer wants, what was agreed, ordered or booked, and what is still open. No "
        "greeting."
    ),
    "draft": (
        "[For the team, not the customer] Write the next message the team could send this "
        "customer, in the customer's language and letters and the business's tone. Only the "
        "message: the team checks it before sending. Never promise what the chat and the "
        "business's details don't say."
    ),
    "ask": (
        "[For the team, not the customer] A team member asks about this chat: {question}\n"
        "Answer in one or two lines from the chat and the business's details; if they don't "
        "say, say so."
    ),
}

MAX_SNOOZE_DAYS = 30  # the furthest a chat can be snoozed (I3)
OPEN = ("needs_human", "human")  # a chat that's open for its team member: waiting, or theirs (I1)


@dataclass
class RunningTurn:
    conversation_id: str
    events: AsyncGenerator[Event]  # the listener reads these; the turn is saved at the end
    interrupt: asyncio.Event  # set it to stop the turn at the next token (a barge-in)


@dataclass(frozen=True)
class Reply:
    """A whole turn's reply, for a channel that sends messages, not a stream (Telegram)."""

    text: str  # empty when the turn failed: the hand-off says what to do
    handoff: HandOff | None
    photos: list[dict[str, str]] = field(default_factory=list)  # shown by a tool (D62)
    choices: list[str] = field(default_factory=list)  # buttons under the text (D99)
    card: Card | None = None  # the last card a tool showed (D110), sent its own way (D111)
    conversation_id: str = ""  # a product card's page links back to it (D130)


class ConversationService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        runtime: Runtime,
        knowledge: KnowledgeService,
        photos: PhotoService,
        team: TeamService,
        visitors: VisitorService,
        woo: WooService,
        calls: CallService,
        settings: Settings,
        now: Callable[[], datetime],
    ) -> None:
        self.sessions = sessions
        self.runtime = runtime
        self.knowledge = knowledge
        self.photos = photos
        self.team = team
        self.visitors = visitors
        self.woo = woo
        self.calls = calls
        self.settings = settings
        self.now = now

    async def start_turn(
        self,
        agent: LiveAgent,
        conversation_id: str | None,
        text: str,
        channel: str,
        visitor: str,
        contact: str | None = None,
        photo: bytes | None = None,
        name: str | None = None,
    ) -> RunningTurn:
        """Loads the conversation (or starts one), then runs the turn as the listener reads it.
        `visitor` is who is talking, for the limits (D51): `web:<token>`, `telegram:<chat id>`.
        A messaging app passes its `contact` (the chat id) and their `name`, if it knows it, for a
        new conversation; the web chat and a call pass the browser's token (D114). A web visitor
        can send a `photo` with it, or instead of words, when the desk takes photos (D57)."""
        text = text.strip()
        if photo is not None and not agent.desk.takes_photos:
            raise UnreadablePhoto("This chat can't take photos.")
        if not (text or photo) or len(text) > self.settings.MAX_MESSAGE_CHARS:
            raise HeyDeskError(f"A message is 1 to {self.settings.MAX_MESSAGE_CHARS} characters.")
        runs_ai = await self.visitors.let_in(agent, visitor)  # a 429 when they're blocked (I2)
        # Read before anything is saved, so a file that isn't a photo changes nothing
        saved = await self.photos.save(agent.workspace_id, "chat", photo) if photo else None
        conv = None
        if conversation_id:
            async with self.sessions() as s:
                conv = await self._get(s, agent, conversation_id)
            if self._is_full(conv):
                raise ConversationFull("This conversation is very long. Please start a new one.")
            # the team is answering: the agent stays quiet (D43), until it's quiet (D135)
            if conv.status == HUMAN and not await self._given_back(conv):
                return RunningTurn(conv.id, self._to_team(conv, text, channel), asyncio.Event())
        if not runs_ai:  # the AI is off for them (I2): the team has the chat, no turn is paid
            conv = await self._for_team(agent, conv, channel, contact, name)
            return RunningTurn(conv.id, self._to_team(conv, text, channel), asyncio.Event())
        await self.visitors.admit(agent, visitor)  # counts the turn, or refuses it (D51)
        if conv is None:
            conv = self._new(agent, channel, contact, name)
            async with self.sessions() as s, s.begin():
                s.add(conv)

        now = self.now()
        desk, config = agent.desk, agent.config
        store = desk.store(
            self.sessions,
            agent.workspace_id,
            self.now,
            conversation_id=conv.id,
            knowledge=self.knowledge,
            public_url=self.settings.public_url,
        )
        photo_line = ""
        if saved:  # kept for the owner's view; the model gets a line on its closest products
            matches = await self.photos.nearest(agent.workspace_id, saved.jpeg)
            photo_line = await desk.photo_line(store, matches)
        turn = TurnInput(
            system=build_system_prompt(
                config,
                await desk.prompt(config, store),
                channel,
                now,
                agent.documents,
                self.settings.VOICE_LANGUAGE,
                await store.remembered(),  # what they told the agent before (D124)
            ),
            tools=tools_for(desk, config, has_documents=bool(agent.documents)),
            model=self.settings.MODEL,
            history=conv.messages,
            user_text=text,
            channel=channel,
            agent=config,
            store=store,
            now=now,
            state=conv.state,
            note=lambda state: desk.note(config, state),
            is_first=not conv.messages,
            photo_url=saved.url if saved else "",
            photo_line=photo_line,
        )
        return RunningTurn(conv.id, self._run(turn, conv, agent, visitor), turn.interrupt)

    async def reply(
        self,
        agent: LiveAgent,
        channel: str,
        contact: str,
        text: str,
        photo: bytes | None = None,
        name: str | None = None,
    ) -> Reply:
        """For a messaging app (D42): continues the contact's conversation, or starts one, and
        returns the whole reply. A `photo` is the one the visitor sent (Messenger, D62). A conversation that is full, or quiet for
        CONVERSATION_IDLE_HOURS, is left behind: yesterday's half-order shouldn't come back."""
        async with self.sessions() as s:
            latest = await s.scalar(
                select(Conversation)
                .where(
                    Conversation.workspace_id == agent.workspace_id,
                    Conversation.agent_id == agent.agent_id,
                    Conversation.channel == channel,
                    Conversation.contact == contact,
                )
                .order_by(Conversation.updated_at.desc())
                .limit(1)
            )
        idle = timedelta(hours=self.settings.CONVERSATION_IDLE_HOURS)
        if latest and (self._is_full(latest) or self.now() - latest.updated_at > idle):
            latest = None
        turn = await self.start_turn(
            agent,
            latest.id if latest else None,
            text,
            channel,
            f"{channel}:{contact}",
            contact,
            photo,
            name,
        )
        parts: list[str] = []
        photos: list[dict[str, str]] = []
        handoff = None
        card = None
        choices: list[str] = []
        async with aclosing(turn.events) as events:
            async for event in events:
                if isinstance(event, Token):
                    parts.append(event.text)
                elif isinstance(event, HandOff):
                    handoff = event
                elif isinstance(event, Photos):
                    photos.extend(event.items)
                elif isinstance(event, Card):
                    card = event
                elif isinstance(event, TurnEnded):
                    choices = event.choices
        return Reply("".join(parts).strip(), handoff, photos, choices, card, turn.conversation_id)

    async def restart(
        self, agent: LiveAgent, channel: str, contact: str, name: str | None = None
    ) -> None:
        """A new, empty conversation for the contact (Telegram's /start); their next message
        goes into it, after the greeting."""
        async with self.sessions() as s, s.begin():
            s.add(self._new(agent, channel, contact, name))

    async def team_replies(self, agent: LiveAgent, conversation_id: str) -> list[str]:
        """What the team has written in a web chat, oldest first; the page shows the new ones."""
        async with self.sessions() as s:
            conv = await self._get(s, agent, conversation_id)
        return [m["content"] for m in conv.messages if m.get("by") == TEAM]

    async def store_for(
        self, agent: LiveAgent, conversation_id: str
    ) -> tuple[Store, Conversation | None]:
        """The agent's data for an order from a product's page (D130): linked to the chat the
        page was opened from when `conversation_id` is one of this agent's, else to none."""
        conv = None
        if conversation_id:
            async with self.sessions() as s:
                with suppress(NotFound):
                    conv = await self._get(s, agent, conversation_id)
        store = agent.desk.store(
            self.sessions,
            agent.workspace_id,
            self.now,
            conversation_id=conv.id if conv else None,
            public_url=self.settings.public_url,
        )
        return store, conv

    async def list(
        self,
        ws: str,
        status: str | None = None,
        *,
        channel: str | None = None,
        assigned_to: str | None = None,
        unassigned: bool = False,
        q: str = "",
        done: bool = False,
        label: str = "",
        limit: int = 100,
        before: datetime | None = None,
    ) -> list[Conversation]:
        """Newest activity first, a page at a time: the next page is `before=` the last
        one's `updated_at`. `assigned_to` a team member, or `unassigned` (I1). Chats that are
        done or snoozed are left out, or only the done ones with `done`; a search (`q`: the
        name, or anything said, such as a phone or an order code) looks through all (I3).
        `label`: only the chats with it (D136)."""
        query = (
            select(Conversation)
            .where(Conversation.workspace_id == ws)
            .order_by(Conversation.updated_at.desc())
            .limit(limit)
        )
        if status:
            query = query.where(Conversation.status == status)
        if channel:
            query = query.where(Conversation.channel == channel)
        if assigned_to:
            query = query.where(Conversation.assigned_to == assigned_to)
        if unassigned:
            query = query.where(Conversation.assigned_to.is_(None))
        if label:  # the list as JSON holds it in quotes: ["vip", "cod"]
            query = query.where(cast(Conversation.labels, String).contains(json.dumps(label)))
        if q.strip():
            like = (
                "%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            )
            query = query.where(
                or_(
                    Conversation.name.ilike(like, escape="\\"),
                    cast(Conversation.messages, String).ilike(like, escape="\\"),
                )
            )
        elif done:
            query = query.where(Conversation.done_at.is_not(None))
        else:
            query = query.where(*self._showing())
        if before:
            query = query.where(Conversation.updated_at < before)
        async with self.sessions() as s:
            return list(await s.scalars(query))

    async def counts(self, ws: str, user_id: str) -> dict[str, int]:
        """How many shown chats wait for a person: all of them, the caller's, and no one's (I1)."""
        waiting = Conversation.status == "needs_human"
        query = select(
            func.count().filter(waiting),
            func.count().filter(waiting, Conversation.assigned_to == user_id),
            func.count().filter(waiting, Conversation.assigned_to.is_(None)),
        ).where(Conversation.workspace_id == ws, *self._showing())
        async with self.sessions() as s:
            needs_human, mine, unassigned = (await s.execute(query)).one()
        return {"needs_human": needs_human, "mine": mine, "unassigned": unassigned}

    async def open_chats(self, ws: str) -> dict[str, int]:
        """Each team member's open chats, the number a hand-off goes by (I1); none: not there."""
        query = (
            select(Conversation.assigned_to, func.count())
            .where(
                Conversation.workspace_id == ws,
                Conversation.assigned_to.is_not(None),
                Conversation.status.in_(OPEN),
            )
            .group_by(Conversation.assigned_to)
        )
        async with self.sessions() as s:
            return {user_id: n for user_id, n in (await s.execute(query)).all() if user_id}

    async def get(self, ws: str, conversation_id: str) -> Conversation:
        async with self.sessions() as s:
            return await self._get_by_id(s, ws, conversation_id)

    async def of_visitor(self, ws: str, key: str) -> Conversation | None:
        """The visitor's latest conversation (`key` as `visitors.key_of` makes it), for the
        blocked list (I2)."""
        kind, contact = key.split(":", 1)
        channels = ("web", "voice") if kind == "web" else (kind,)
        async with self.sessions() as s:
            return await s.scalar(
                select(Conversation)
                .where(
                    Conversation.workspace_id == ws,
                    Conversation.channel.in_(channels),
                    Conversation.contact == contact,
                )
                .order_by(Conversation.updated_at.desc())
                .limit(1)
            )

    async def assign(
        self, ws: str, conversation_id: str, by: Principal, user_id: str | None
    ) -> Conversation:
        """Who has the chat (I1): the owner (or an admin) gives any chat to anyone on the team,
        or no one; a moderator takes an unassigned one for themself and gives back their own."""
        async with self.sessions() as s, s.begin():
            conv = await self._get_by_id(s, ws, conversation_id)
            if by.role == "moderator":
                takes = user_id == by.user_id and conv.assigned_to in (None, by.user_id)
                gives_back = user_id is None and conv.assigned_to in (None, by.user_id)
                if not (takes or gives_back):
                    raise Forbidden("Only the owner can give a chat to someone else.")
            if user_id is not None:
                member = select(User.id).where(User.id == user_id, User.workspace_id == ws)
                if await s.scalar(member) is None:
                    raise NotFound("There's no such member on your team.")
            conv.assigned_to = user_id
        log.info("assigned", extra={"conversation_id": conv.id})
        return conv

    async def mark_done(self, ws: str, conversation_id: str, done: bool) -> Conversation:
        """Done hides the chat until the visitor writes again; not done shows it again (I3)."""
        async with self.sessions() as s, s.begin():
            conv = await self._get_by_id(s, ws, conversation_id)
            conv.done_at = self.now() if done else None
            if done:
                conv.snoozed_until = None
        return conv

    async def snooze(self, ws: str, conversation_id: str, until: datetime | None) -> Conversation:
        """Hides the chat until `until`, when it comes back with an alert (I3); None: back now.
        A time not in the future, or more than `MAX_SNOOZE_DAYS` ahead, is refused."""
        now = self.now()
        if until is not None and not now < until <= now + timedelta(days=MAX_SNOOZE_DAYS):
            raise InvalidConfig({"until": f"Pick a time in the next {MAX_SNOOZE_DAYS} days."})
        async with self.sessions() as s, s.begin():
            conv = await self._get_by_id(s, ws, conversation_id)
            conv.snoozed_until = until
            conv.done_at = None
        return conv

    async def run(self, every: float) -> None:
        """Brings snoozed chats back when they're due, every `every` seconds, until cancelled
        (the app's lifespan starts it)."""
        while True:
            await asyncio.sleep(every)
            try:
                await self.wake_snoozed()
            except Exception:
                log.exception("snoozed chats not woken")

    async def wake_snoozed(self) -> None:
        """Each snoozed chat that's due is back in the list, and the owner hears about it."""
        async with self.sessions() as s, s.begin():
            due = list(
                await s.scalars(
                    select(Conversation).where(Conversation.snoozed_until <= self.now())
                )
            )
            for conv in due:
                conv.snoozed_until = None
        for conv in due:
            await self.team.alert(conv, "⏰ A chat you snoozed is back.")
            log.info("snooze over", extra={"conversation_id": conv.id})

    async def hand_back(self, ws: str, conversation_id: str) -> Conversation:
        """The owner dealt with a hand-off from the console: the agent answers again, as
        after `/done` on Telegram (D83)."""
        async with self.sessions() as s, s.begin():
            conv = await self._get_by_id(s, ws, conversation_id)
            if conv.status == "active":
                raise InvalidTransition("This conversation isn't waiting for a person.")
            conv.status, conv.taken_over_at = "active", None
        log.info("hand-off handled", extra={"conversation_id": conv.id})
        return conv

    async def set_labels(
        self, ws: str, conversation_id: str, labels: Sequence[str]
    ) -> Conversation:
        """The team's own words for a chat (D136): "vip", "fake order". Kept in order, once
        each; the visitor and the model never see them."""
        async with self.sessions() as s, s.begin():
            conv = await self._get_by_id(s, ws, conversation_id)
            kept = dict.fromkeys(" ".join(lb.split()).lower() for lb in labels if lb.strip())
            conv.labels = [*kept]
        return conv

    async def notes(self, ws: str, conversation_id: str) -> Sequence[Note]:
        """The team's notes on a chat, oldest first (D136)."""
        async with self.sessions() as s:
            await self._get_by_id(s, ws, conversation_id)  # 404 for another business's
            rows = await s.scalars(
                select(Note)
                .where(Note.workspace_id == ws, Note.conversation_id == conversation_id)
                .order_by(Note.created_at)
            )
            return rows.all()

    async def add_note(self, ws: str, conversation_id: str, author: str, text: str) -> Note:
        """A note for the team (D136): its own table, added and never edited (rule 5), and
        never in the transcript, so the visitor and the model don't see it."""
        async with self.sessions() as s, s.begin():
            await self._get_by_id(s, ws, conversation_id)
            note = Note(
                workspace_id=ws,
                conversation_id=conversation_id,
                author=author,
                text=text.strip(),
                created_at=self.now(),
            )
            s.add(note)
        return note

    async def team_reply(self, ws: str, conversation_id: str, text: str) -> Conversation:
        """The team answers from the inbox (D136), as from a Telegram alert: the words go to
        the visitor and into the transcript as the team's, and the chat is taken over, until
        given back or 30 quiet minutes (D135). A call can't take one."""
        async with self.sessions() as s, s.begin():
            conv = await self._get_by_id(s, ws, conversation_id)
            if conv.channel == "voice":
                raise InvalidTransition("That was a phone call: call them back instead.")
            said = {**self.runtime.llm.assistant_message(text.strip()), "by": TEAM}
            conv.messages = [*conv.messages, said]  # a new list, so SQLAlchemy sees it
            conv.status, conv.taken_over_at, conv.updated_at = HUMAN, self.now(), self.now()
        await self.team.to_visitor(conv, text.strip())
        log.info("team replied", extra={"conversation_id": conv.id})
        return conv

    async def assist(
        self, agent: LiveAgent, conversation_id: str, kind: str, question: str = ""
    ) -> str:
        """The model's help for the team on a chat (D137): a summary, an answer about it, or a
        draft of the next reply. The chat's own prompt and history, no tools, and one line of
        what's wanted; the words come back to the team and nothing is added to the chat or
        sent. Traced and billed as a turn, on the channel "assist"."""
        ws = agent.workspace_id
        async with self.sessions() as s:
            conv = await self._get_by_id(s, ws, conversation_id)
        desk, config = agent.desk, agent.config
        store = desk.store(self.sessions, ws, self.now, conversation_id=conv.id)
        asked = ASSIST[kind].replace("{question}", " ".join(question.split())[:500])
        turn = TurnInput(
            system=build_system_prompt(
                config,
                await desk.prompt(config, store),
                conv.channel,
                self.now(),
                agent.documents,
                self.settings.VOICE_LANGUAGE,
            ),
            tools=[],  # it only writes words: nothing is looked up, saved or sent
            model=self.settings.MODEL,
            history=conv.messages,
            user_text=asked,
            channel="assist",
            agent=config,
            store=store,
            now=self.now(),
            state=conv.state,
        )
        parts: list[str] = []
        ended = None
        async with aclosing(run_turn(turn, self.runtime)) as events:
            async for event in events:
                if isinstance(event, Token):
                    parts.append(event.text)
                elif isinstance(event, TurnEnded):
                    ended = event
        if ended is not None:
            async with self.sessions() as s, s.begin():
                s.add(self._turn(conv, ended))
        text = "".join(parts).strip()
        if ended is None or ended.handed_off or not text:
            raise AssistUnavailable()
        log.info("assist", extra={"conversation_id": conv.id, "kind": kind})
        return text

    async def take_over(self, ws: str, conversation_id: str, on: bool) -> Conversation:
        """The team takes the chat, and the agent stays quiet in it, or gives it back (D135):
        the inbox's switch, "." in Business Suite or on Telegram. Quiet for
        TAKEOVER_QUIET_MINUTES, it's the agent's again."""
        async with self.sessions() as s, s.begin():
            conv = await self._get_by_id(s, ws, conversation_id)
            conv.status = HUMAN if on else "active"
            conv.taken_over_at = self.now() if on else None
        log.info("taken over" if on else "given back", extra={"conversation_id": conv.id})
        return conv

    async def page_wrote(self, agent: LiveAgent, channel: str, contact: str, text: str) -> None:
        """The business wrote to a buyer from its own app, not through us (D135): Business
        Suite or Messenger. "." takes the chat over or gives it back; other words are added to
        the chat as the team's (rule 5), so the agent reads them next turn, and keep a chat
        that's taken over from going quiet."""
        async with self.sessions() as s:
            conv = await s.scalar(
                select(Conversation)
                .where(
                    Conversation.workspace_id == agent.workspace_id,
                    Conversation.agent_id == agent.agent_id,
                    Conversation.channel == channel,
                    Conversation.contact == contact,
                )
                .order_by(Conversation.updated_at.desc())
                .limit(1)
            )
        if conv is None:  # they wrote first: there's nothing to add it to yet
            return
        if text.strip() == ".":
            await self.take_over(agent.workspace_id, conv.id, conv.status != HUMAN)
            return
        async with self.sessions() as s, s.begin():
            saved = await self._get_by_id(s, agent.workspace_id, conv.id)
            said = {**self.runtime.llm.assistant_message(text), "by": TEAM}
            saved.messages = [*saved.messages, said]  # a new list, so SQLAlchemy sees it
            saved.updated_at = self.now()
            if saved.taken_over_at is not None:
                saved.taken_over_at = self.now()

    async def _given_back(self, conv: Conversation) -> bool:
        """A chat the team took over and then left quiet is the agent's again (D135)."""
        at = conv.taken_over_at
        if at is None:
            return False
        quiet = timedelta(minutes=self.settings.TAKEOVER_QUIET_MINUTES)
        if self.now() - (at if at.tzinfo else at.replace(tzinfo=UTC)) < quiet:
            return False
        await self.take_over(conv.workspace_id, conv.id, False)
        conv.status, conv.taken_over_at = "active", None
        return True

    async def _run(
        self, turn: TurnInput, conv: Conversation, agent: LiveAgent, visitor: str
    ) -> AsyncGenerator[Event]:
        """Passes the turn's events to the listener and saves the turn when it ends.

        The listener drives this, so a tab closed mid-reply loses the turn (D3).
        """
        async with aclosing(run_turn(turn, self.runtime)) as events:
            async for event in events:
                if isinstance(event, TurnEnded):
                    await self._save(conv, event, agent.config.handoff.auto_assign)
                    await self._alert(conv, event)
                    if event.alerts:  # a new shop order: its buyer may get a call (D89)
                        await self.calls.after_turn(agent, conv.id)
                    await self.visitors.after_turn(agent, visitor, event.tools)
                yield event

    async def _for_team(
        self,
        agent: LiveAgent,
        conv: Conversation | None,
        channel: str,
        contact: str | None,
        name: str | None,
    ) -> Conversation:
        """The conversation (a new one if none) with the team answering it (I2)."""
        async with self.sessions() as s, s.begin():
            if conv is None:
                conv = self._new(agent, channel, contact, name)
                s.add(conv)
            else:
                (await self._get_by_id(s, agent.workspace_id, conv.id)).status = HUMAN
            conv.status = HUMAN
        return conv

    async def _to_team(self, conv: Conversation, text: str, channel: str) -> AsyncGenerator[Event]:
        """A turn with no agent: the message is saved and passed to the team."""
        message = self.runtime.llm.user_message(text)
        ended = TurnEnded([message], conv.state, {"channel": channel, "to_team": True})
        await self._save(conv, ended)
        await self.team.forward(conv, text)
        yield ended

    async def _alert(self, conv: Conversation, ended: TurnEnded) -> None:
        """Tells the owner what the turn saved, and if the visitor wants a person."""
        for alert in ended.alerts:
            await self.team.alert(conv, alert)
        if ended.alerts:  # an order saved or changed: into a linked WooCommerce store now (D77)
            self.woo.push_soon(conv.workspace_id)
        if ended.handed_off:
            await self.team.alert(conv, handoff_alert([*conv.messages, *ended.added]))

    async def _save(self, conv: Conversation, ended: TurnEnded, auto_assign: bool = False) -> None:
        async with self.sessions() as s, s.begin():
            saved = await self._get_by_id(s, conv.workspace_id, conv.id)
            saved.messages = [*saved.messages, *ended.added]  # a new list, so SQLAlchemy sees it
            saved.state = ended.state
            saved.done_at = saved.snoozed_until = None  # they wrote again: it's back (I3)
            if ended.handed_off:
                saved.status = "needs_human"
                if auto_assign and saved.assigned_to is None:
                    saved.assigned_to = await least_busy(s, conv.workspace_id)
            saved.updated_at = self.now()
            if not ended.trace.get("to_team"):  # a message to the team isn't an agent turn
                s.add(self._turn(conv, ended))
        log.info("turn finished", extra={"conversation_id": conv.id, "trace": ended.trace})

    def _turn(self, conv: Conversation, ended: TurnEnded) -> Turn:
        """The turn's trace as a row (D71), so the admin can add it up. No one's words."""
        trace = ended.trace
        tokens = [step["tokens"] for step in trace["steps"] if "tokens" in step]
        return Turn(
            workspace_id=conv.workspace_id,
            conversation_id=conv.id,
            channel=trace["channel"],
            llm=trace.get("llm", ""),
            at=self.now(),
            total_ms=trace["total_ms"],
            first_output_ms=trace["first_output_ms"],
            input_tokens=sum(t["input"] for t in tokens),
            output_tokens=sum(t["output"] for t in tokens),
            cost_micros=trace["cost_micros"],
            tools=ended.tools,
            error=(trace["error"] or "")[:300],
            handed_off=ended.handed_off,
            interrupted=ended.interrupted,
        )

    def _new(
        self, agent: LiveAgent, channel: str, contact: str | None, name: str | None = None
    ) -> Conversation:
        return Conversation(
            workspace_id=agent.workspace_id,
            agent_id=agent.agent_id,
            channel=channel,
            contact=contact,
            name=(name or "").strip()[:100] or None,
            state={},
            messages=[],
            created_at=self.now(),
            updated_at=self.now(),
        )

    def _showing(self) -> tuple[ColumnElement[bool], ...]:
        """The usual list's chats: not done, and not snoozed (I3)."""
        return (
            Conversation.done_at.is_(None),
            or_(Conversation.snoozed_until.is_(None), Conversation.snoozed_until <= self.now()),
        )

    def _is_full(self, conv: Conversation) -> bool:
        turns = sum(1 for m in conv.messages if m.get("role") == "user")
        return turns >= self.settings.MAX_TURNS

    async def _get(self, s: AsyncSession, agent: LiveAgent, conversation_id: str) -> Conversation:
        conv = await self._get_by_id(s, agent.workspace_id, conversation_id)
        if conv.agent_id != agent.agent_id:
            raise NotFound("There's no such conversation. Start a new one.")
        return conv

    async def _get_by_id(self, s: AsyncSession, ws: str, conversation_id: str) -> Conversation:
        conv = await s.scalar(
            select(Conversation).where(
                Conversation.id == conversation_id, Conversation.workspace_id == ws
            )
        )
        if conv is None:
            raise NotFound("There's no such conversation. Start a new one.")
        return conv


async def least_busy(s: AsyncSession, ws: str) -> str | None:
    """The joined moderator with the fewest open chats (waiting for a person, or with the team),
    the longest on the team first when it's even; None with no moderator (I1)."""
    open_chats = (
        select(func.count())
        .select_from(Conversation)
        .where(
            Conversation.workspace_id == ws,
            Conversation.assigned_to == User.id,
            Conversation.status.in_(OPEN),
        )
        .correlate(User)
        .scalar_subquery()
    )
    moderators = select(User.id).where(
        User.workspace_id == ws, User.role == "moderator", User.email_verified_at.is_not(None)
    )
    return await s.scalar(moderators.order_by(open_chats, User.created_at).limit(1))
