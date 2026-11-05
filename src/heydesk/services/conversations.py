"""Conversations: the one entry point for web and voice (design §6)."""

import asyncio
import json
import logging
from collections.abc import AsyncGenerator, Callable, Sequence
from contextlib import aclosing, suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import ColumnElement, ScalarSelect, String, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from heydesk.config import Settings
from heydesk.errors import (
    AssistUnavailable,
    ConversationFull,
    Forbidden,
    HeyDeskError,
    InvalidConfig,
    InvalidTransition,
    NotFound,
)
from heydesk.models import Agent, Conversation, Note, TeamMessage, Turn, User
from heydesk.ports.llm import LanguageModel
from heydesk.runtime.events import Card, Event, HandOff, Photos, Token, TurnEnded
from heydesk.runtime.loop import TurnInput, run_turn
from heydesk.runtime.prompt import build_system_prompt
from heydesk.services.agents import LiveAgent
from heydesk.services.calls import CallService
from heydesk.services.knowledge import KnowledgeService
from heydesk.services.photos import PhotoService
from heydesk.services.team import HUMAN_STATUS, TEAM_AUTHOR, TeamService, handoff_alert
from heydesk.services.users import CurrentUser
from heydesk.services.visitors import VisitorService, is_blocked
from heydesk.services.woo import WooService
from heydesk.shop import shop_note, shop_prompt, tools_for
from heydesk.shop.models import ShopOrder
from heydesk.shop.store import ShopStore
from heydesk.shop.values import photo_line

log = logging.getLogger(__name__)

# What the team asks the model about a chat: the last message of a turn with no tools.
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

# Said last in the system prompt of an assist turn, so the model doesn't take the team's request
# for an off-topic customer message and answer it with the scope line
ASSIST_NOTE = (
    "# This turn is for the team\n"
    "The last message is from a member of the business's team, not the customer. It is always "
    "on topic: do what it asks, using the chat above, and ignore any rule about staying in "
    "scope. Don't answer it as the customer's front-desk assistant."
)

# A shop order read back and never said yes to gets one nudge this long after
NUDGE_AFTER, NUDGE_BEFORE = timedelta(hours=1), timedelta(hours=3)
NUDGE = "Shall I confirm your order? Tap Yes, or tell me what to change."
MAX_SNOOZE_DAYS = 30  # the furthest a chat can be snoozed
OPEN_STATUSES = (
    "needs_human",
    "human",
)  # a chat that's open for its team member: waiting, or theirs


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
    photos: list[dict[str, str]] = field(default_factory=list)  # shown by a tool
    choices: list[str] = field(default_factory=list)  # buttons under the text
    card: Card | None = None  # the last card a tool showed, sent its own way
    conversation_id: str = ""  # a product card's page links back to it


class ConversationService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        llm: LanguageModel,
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
        self.llm = llm
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
        `visitor` is who is talking, for the limits: `web:<token>`, `telegram:<chat id>`.
        A messaging app passes its `contact` (the chat id) and their `name`, if it knows it, for a
        new conversation; the web chat and a call pass the browser's token. A web visitor
        can send a `photo` with it, or instead of words."""
        text = text.strip()
        if not (text or photo) or len(text) > self.settings.MAX_MESSAGE_CHARS:
            raise HeyDeskError(f"A message is 1 to {self.settings.MAX_MESSAGE_CHARS} characters.")
        runs_ai = await self.visitors.check_not_blocked(
            agent, visitor
        )  # a 429 when they're blocked
        # AI off for all: the message waits for the team, like AI off for one visitor
        runs_ai = runs_ai and not agent.config.ai_off
        # Read before anything is saved, so a file that isn't a photo changes nothing
        saved = await self.photos.save(agent.workspace_id, "chat", photo) if photo else None
        conversation = None
        if conversation_id:
            async with self.sessions() as s:
                conversation = await self._get(s, agent, conversation_id)
            if self._is_full(conversation):
                raise ConversationFull("This conversation is very long. Please start a new one.")
        if not runs_ai:  # the AI is off for them: the team has the chat, no turn is paid
            conversation = await self._conversation_for_team(
                agent, conversation, channel, contact, name
            )
            return RunningTurn(
                conversation.id, self._save_for_team(conversation, text, channel), asyncio.Event()
            )
        # the team is answering: the agent stays quiet, until it's quiet
        if (
            conversation is not None
            and conversation.status == HUMAN_STATUS
            and not await self._return_if_quiet(conversation)
        ):
            return RunningTurn(
                conversation.id, self._save_for_team(conversation, text, channel), asyncio.Event()
            )
        await self.visitors.count_turn(agent, visitor)  # counts the turn, or refuses it
        if conversation is None:
            conversation = self._new_conversation(agent, channel, contact, name)
            async with self.sessions() as s, s.begin():
                s.add(conversation)

        now = self.now()
        config = agent.config
        store = ShopStore(
            self.sessions,
            agent.workspace_id,
            self.now,
            conversation_id=conversation.id,
            knowledge=self.knowledge,
            public_url=self.settings.public_url,
        )
        about_photo = ""
        if saved:  # kept for the owner's view; the model gets a line on its closest products
            matches = await self.photos.nearest(agent.workspace_id, saved.jpeg)
            about_photo = photo_line(await store.products(), matches)
        turn = TurnInput(
            system=build_system_prompt(
                config,
                await shop_prompt(config, store),
                channel,
                now,
                agent.documents,
                self.settings.VOICE_LANGUAGE,
                await store.visitor_notes(),  # what they told the agent before
            ),
            tools=tools_for(has_documents=bool(agent.documents)),
            model=self.settings.MODEL,
            history=conversation.messages[conversation.history_from :],  # what the agent remembers
            user_text=text,
            channel=channel,
            agent=config,
            store=store,
            now=now,
            state=conversation.state,
            note=lambda state: shop_note(config, state),
            is_first=len(conversation.messages) == conversation.history_from,
            photo_url=saved.url if saved else "",
            photo_line=about_photo,
        )
        return RunningTurn(
            conversation.id, self._run_turn(turn, conversation, agent, visitor), turn.interrupt
        )

    async def reply(
        self,
        agent: LiveAgent,
        channel: str,
        contact: str,
        text: str,
        photo: bytes | None = None,
        name: str | None = None,
    ) -> Reply:
        """For a messaging app: continues the contact's conversation, or starts one, and
        returns the whole reply. A `photo` is the one the visitor sent (Messenger). One
        that's full or quiet starts afresh in the same chat."""
        latest = await self._latest_conversation(agent, channel, contact)
        if latest is not None and self._stale(latest):
            await self._start_afresh(latest)
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

    async def will_answer(self, agent: LiveAgent, channel: str, contact: str) -> bool:
        """Whether the agent will answer the contact's next message on a messaging app, so the
        app shows "typing…" only then: not blocked, the AI on for them and for the business, and
        not a chat the team has."""
        visitor = f"{channel}:{contact}"
        found = await self.visitors.find(agent.workspace_id, visitor)
        if found is not None and (found.ai_off or is_blocked(found, self.now())):
            return False
        if agent.config.ai_off:
            return False
        latest = await self._latest_conversation(agent, channel, contact)
        return latest is None or not self._team_is_answering(latest)

    async def _latest_conversation(
        self, agent: LiveAgent, channel: str, contact: str
    ) -> Conversation | None:
        """The contact's conversation on a messaging app, or None. They keep one: the
        newest, for chats split before that."""
        async with self.sessions() as s:
            return await s.scalar(
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

    def _stale(self, conversation: Conversation) -> bool:
        """Full, or quiet for CONVERSATION_IDLE_HOURS: yesterday's half-order shouldn't come
        back, so the agent starts afresh."""
        idle = timedelta(hours=self.settings.CONVERSATION_IDLE_HOURS)
        at = (
            conversation.updated_at
            if conversation.updated_at.tzinfo
            else conversation.updated_at.replace(tzinfo=UTC)
        )
        return self._is_full(conversation) or self.now() - at > idle

    async def _start_afresh(self, conversation: Conversation) -> None:
        """The agent forgets what came before, in the same chat: its memory starts after
        the last message, the cart is emptied and an unfinished order may be nudged again. The
        team still sees the whole chat, and its labels stay."""
        async with self.sessions() as s, s.begin():
            saved = await self._get_by_id(s, conversation.workspace_id, conversation.id, lock=True)
            saved.history_from = len(saved.messages)
            saved.state, saved.nudged = {}, False
        conversation.history_from, conversation.state, conversation.nudged = (
            len(conversation.messages),
            {},
            False,
        )
        log.info("started afresh", extra={"conversation_id": conversation.id})

    async def restart(
        self, agent: LiveAgent, channel: str, contact: str, name: str | None = None
    ) -> None:
        """Telegram's /start: the contact's chat starts afresh, or a new one if they have
        none; their next message gets the greeting."""
        latest = await self._latest_conversation(agent, channel, contact)
        if latest is not None:
            await self._start_afresh(latest)
            return
        async with self.sessions() as s, s.begin():
            s.add(self._new_conversation(agent, channel, contact, name))

    async def team_replies(self, agent: LiveAgent, conversation_id: str) -> list[str]:
        """What the team has written in a web chat, oldest first; the page shows the new ones."""
        async with self.sessions() as s:
            conversation = await self._get(s, agent, conversation_id)
        return [m["content"] for m in conversation.messages if m.get("by") == TEAM_AUTHOR]

    async def store_for(
        self, agent: LiveAgent, conversation_id: str
    ) -> tuple[ShopStore, Conversation | None]:
        """The agent's data for an order from a product's page: linked to the chat the
        page was opened from when `conversation_id` is one of this agent's, else to none."""
        conversation = None
        if conversation_id:
            async with self.sessions() as s:
                with suppress(NotFound):
                    conversation = await self._get(s, agent, conversation_id)
        store = ShopStore(
            self.sessions,
            agent.workspace_id,
            self.now,
            conversation_id=conversation.id if conversation else None,
            public_url=self.settings.public_url,
        )
        return store, conversation

    async def list(
        self,
        workspace_id: str,
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
        one's `updated_at`. `assigned_to` a team member, or `unassigned`. Chats that are
        done or snoozed are left out, or only the done ones with `done`; a search (`q`: the
        name, or anything said, such as a phone or an order code) looks through all.
        `label`: only the chats with it."""
        query = (
            select(Conversation)
            .where(Conversation.workspace_id == workspace_id)
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
            query = query.where(*self._active_chats_filter())
        if before:
            query = query.where(Conversation.updated_at < before)
        async with self.sessions() as s:
            return list(await s.scalars(query))

    async def counts(self, workspace_id: str, user_id: str) -> dict[str, int]:
        """How many shown chats wait for a person: all of them, the caller's, and no one's.
        And the orders and messages still new, so the inbox counts them without listing them."""
        waiting = Conversation.status == "needs_human"

        def new(table: type[ShopOrder | TeamMessage]) -> ScalarSelect[int]:
            where = table.workspace_id == workspace_id, table.status == "new"
            return select(func.count()).where(*where).scalar_subquery()

        query = select(
            func.count().filter(waiting),
            func.count().filter(waiting, Conversation.assigned_to == user_id),
            func.count().filter(waiting, Conversation.assigned_to.is_(None)),
            new(ShopOrder),
            new(TeamMessage),
        ).where(Conversation.workspace_id == workspace_id, *self._active_chats_filter())
        async with self.sessions() as s:
            needs_human, mine, unassigned, orders, messages = (await s.execute(query)).one()
        return {
            "needs_human": needs_human,
            "mine": mine,
            "unassigned": unassigned,
            "orders_new": orders,
            "messages_new": messages,
        }

    async def open_chats(self, workspace_id: str) -> dict[str, int]:
        """Each team member's open chats, the number a hand-off goes by; none: not there."""
        query = (
            select(Conversation.assigned_to, func.count())
            .where(
                Conversation.workspace_id == workspace_id,
                Conversation.assigned_to.is_not(None),
                Conversation.status.in_(OPEN_STATUSES),
            )
            .group_by(Conversation.assigned_to)
        )
        async with self.sessions() as s:
            return {user_id: n for user_id, n in (await s.execute(query)).all() if user_id}

    async def get(self, workspace_id: str, conversation_id: str) -> Conversation:
        async with self.sessions() as s:
            return await self._get_by_id(s, workspace_id, conversation_id)

    async def latest_for_visitor(self, workspace_id: str, key: str) -> Conversation | None:
        """The visitor's latest conversation (`key` as `visitors.key_of` makes it), for the
        blocked list."""
        kind, contact = key.split(":", 1)
        channels = ("web", "voice") if kind == "web" else (kind,)
        async with self.sessions() as s:
            return await s.scalar(
                select(Conversation)
                .where(
                    Conversation.workspace_id == workspace_id,
                    Conversation.channel.in_(channels),
                    Conversation.contact == contact,
                )
                .order_by(Conversation.updated_at.desc())
                .limit(1)
            )

    async def assign(
        self, workspace_id: str, conversation_id: str, by: CurrentUser, user_id: str | None
    ) -> Conversation:
        """Who has the chat: the owner (or an admin) gives any chat to anyone on the team,
        or no one; a moderator takes an unassigned one for themself and gives back their own."""
        async with self.sessions() as s, s.begin():
            conversation = await self._get_by_id(s, workspace_id, conversation_id)
            if by.role == "moderator":
                takes = user_id == by.user_id and conversation.assigned_to in (None, by.user_id)
                gives_back = user_id is None and conversation.assigned_to in (None, by.user_id)
                if not (takes or gives_back):
                    raise Forbidden("Only the owner can give a chat to someone else.")
            if user_id is not None:
                member = select(User.id).where(
                    User.id == user_id, User.workspace_id == workspace_id
                )
                if await s.scalar(member) is None:
                    raise NotFound("There's no such member on your team.")
            conversation.assigned_to = user_id
        log.info("assigned", extra={"conversation_id": conversation.id})
        if user_id is not None and user_id != by.user_id:  # given to them: tell them
            await self.team.alert(
                conversation, handoff_alert(conversation.messages, "👉 This chat is yours now")
            )
        return conversation

    async def mark_done(self, workspace_id: str, conversation_id: str, done: bool) -> Conversation:
        """Done hides the chat until the visitor writes again; not done shows it again."""
        async with self.sessions() as s, s.begin():
            conversation = await self._get_by_id(s, workspace_id, conversation_id)
            conversation.done_at = self.now() if done else None
            if done:
                conversation.snoozed_until = None
        return conversation

    async def snooze(
        self, workspace_id: str, conversation_id: str, until: datetime | None
    ) -> Conversation:
        """Hides the chat until `until`, when it comes back with an alert; None: back now.
        A time not in the future, or more than `MAX_SNOOZE_DAYS` ahead, is refused."""
        now = self.now()
        if until is not None and not now < until <= now + timedelta(days=MAX_SNOOZE_DAYS):
            raise InvalidConfig({"until": f"Pick a time in the next {MAX_SNOOZE_DAYS} days."})
        async with self.sessions() as s, s.begin():
            conversation = await self._get_by_id(s, workspace_id, conversation_id)
            conversation.snoozed_until = until
            conversation.done_at = None
        return conversation

    async def run(self, every: float) -> None:
        """Brings snoozed chats back when they're due, and nudges unfinished orders,
        every `every` seconds, until cancelled (the app's lifespan starts it)."""
        while True:
            await asyncio.sleep(every)
            try:
                await self.wake_snoozed()
                await self.nudge_unfinished()
            except Exception:
                log.exception("snooze and nudge round failed")

    async def nudge_unfinished(self) -> None:
        """A shop order read back and never said yes to, 1–3 hours on: the chat's last
        turn ran `place_order` and no order came of it. One nudge per chat, ever, with the
        read-back's buttons, and only inside the app's window (`tell_visitor_if_allowed`)."""
        now = self.now()
        due = []
        async with self.sessions() as s, s.begin():
            quiet = await s.scalars(
                select(Conversation)
                .join(Agent, Agent.id == Conversation.agent_id)
                .where(
                    Conversation.status == "active",
                    Conversation.nudged.is_(False),
                    Conversation.updated_at.between(now - NUDGE_BEFORE, now - NUDGE_AFTER),
                )
            )
            for conversation in quiet.all():
                last = await s.scalar(
                    select(Turn)
                    .where(Turn.conversation_id == conversation.id, Turn.channel != "assist")
                    .order_by(Turn.at.desc())
                    .limit(1)
                )
                # ponytail: a place_order that only asked for a missing detail counts too
                if last is None or "place_order" not in last.tools:
                    continue
                placed = await s.scalar(
                    select(ShopOrder.id).where(
                        ShopOrder.workspace_id == conversation.workspace_id,
                        ShopOrder.conversation_id == conversation.id,
                        ShopOrder.created_at >= last.at,
                    )
                )
                if placed is None:
                    conversation.nudged = True  # tried, sent or not: never twice
                    due.append(conversation.id)
        for conversation_id in due:
            await self.team.tell_visitor_if_allowed(conversation_id, NUDGE, ["Yes", "Change"])
            log.info("nudged", extra={"conversation_id": conversation_id})

    async def wake_snoozed(self) -> None:
        """Each snoozed chat that's due is back in the list, and the owner hears about it."""
        async with self.sessions() as s, s.begin():
            due = list(
                await s.scalars(
                    select(Conversation).where(Conversation.snoozed_until <= self.now())
                )
            )
            for conversation in due:
                conversation.snoozed_until = None
        for conversation in due:
            await self.team.alert(conversation, "⏰ A chat you snoozed is back.")
            log.info("snooze over", extra={"conversation_id": conversation.id})

    async def hand_back(self, workspace_id: str, conversation_id: str) -> Conversation:
        """The owner dealt with a hand-off from the console: the agent answers again, as
        after `/done` on Telegram."""
        async with self.sessions() as s, s.begin():
            conversation = await self._get_by_id(s, workspace_id, conversation_id)
            if conversation.status == "active":
                raise InvalidTransition("This conversation isn't waiting for a person.")
            conversation.status, conversation.taken_over_at = "active", None
        log.info("hand-off handled", extra={"conversation_id": conversation.id})
        return conversation

    async def set_labels(
        self, workspace_id: str, conversation_id: str, labels: Sequence[str]
    ) -> Conversation:
        """The team's own words for a chat: "vip", "fake order". Kept in order, once
        each; the visitor and the model never see them."""
        async with self.sessions() as s, s.begin():
            conversation = await self._get_by_id(s, workspace_id, conversation_id)
            kept = dict.fromkeys(" ".join(lb.split()).lower() for lb in labels if lb.strip())
            conversation.labels = [*kept]
        return conversation

    async def notes(self, workspace_id: str, conversation_id: str) -> Sequence[Note]:
        """The team's notes on a chat, oldest first."""
        async with self.sessions() as s:
            await self._get_by_id(s, workspace_id, conversation_id)  # 404 for another business's
            rows = await s.scalars(
                select(Note)
                .where(Note.workspace_id == workspace_id, Note.conversation_id == conversation_id)
                .order_by(Note.created_at)
            )
            return rows.all()

    async def add_note(
        self, workspace_id: str, conversation_id: str, author: str, text: str
    ) -> Note:
        """A note for the team: its own table, added and never edited (rule 5), and
        never in the transcript, so the visitor and the model don't see it."""
        async with self.sessions() as s, s.begin():
            await self._get_by_id(s, workspace_id, conversation_id)
            note = Note(
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                author=author,
                text=text.strip(),
                created_at=self.now(),
            )
            s.add(note)
        return note

    async def team_reply(self, workspace_id: str, conversation_id: str, text: str) -> Conversation:
        """The team answers from the inbox, as from a Telegram alert: the words go to
        the visitor and into the transcript as the team's, and the chat is taken over, until
        given back or 30 quiet minutes. A call can't take one."""
        async with self.sessions() as s, s.begin():
            conversation = await self._get_by_id(s, workspace_id, conversation_id, lock=True)
            if conversation.channel == "voice":
                raise InvalidTransition("That was a phone call: call them back instead.")
            said = {**self.llm.assistant_message(text.strip()), "by": TEAM_AUTHOR}
            conversation.messages = [
                *conversation.messages,
                said,
            ]  # a new list, so SQLAlchemy sees it
            conversation.status, conversation.taken_over_at, conversation.updated_at = (
                HUMAN_STATUS,
                self.now(),
                self.now(),
            )
        await self.team.send_to_visitor(conversation, text.strip())
        log.info("team replied", extra={"conversation_id": conversation.id})
        return conversation

    async def assist(
        self, agent: LiveAgent, conversation_id: str, kind: str, question: str = ""
    ) -> str:
        """The model's help for the team on a chat: a summary, an answer about it, or a
        draft of the next reply. The chat's own prompt and history, no tools, and one line of
        what's wanted; the words come back to the team and nothing is added to the chat or
        sent. Traced and billed as a turn, on the channel "assist"."""
        workspace_id = agent.workspace_id
        async with self.sessions() as s:
            conversation = await self._get_by_id(s, workspace_id, conversation_id)
        config = agent.config
        store = ShopStore(self.sessions, workspace_id, self.now, conversation_id=conversation.id)
        asked = ASSIST[kind].replace("{question}", " ".join(question.split())[:500])
        turn = TurnInput(
            system=build_system_prompt(
                config,
                await shop_prompt(config, store),
                conversation.channel,
                self.now(),
                agent.documents,
                self.settings.VOICE_LANGUAGE,
            )
            + "\n\n"
            + ASSIST_NOTE,
            tools=[],  # it only writes words: nothing is looked up, saved or sent
            model=self.settings.MODEL,
            history=conversation.messages[conversation.history_from :],  # what the agent remembers
            user_text=asked,
            channel="assist",
            agent=config,
            store=store,
            now=self.now(),
            state=conversation.state,
        )
        parts: list[str] = []
        ended = None
        async with aclosing(run_turn(turn, self.llm)) as events:
            async for event in events:
                if isinstance(event, Token):
                    parts.append(event.text)
                elif isinstance(event, TurnEnded):
                    ended = event
        if ended is not None:
            async with self.sessions() as s, s.begin():
                s.add(self._turn_row(conversation, ended))
        text = "".join(parts).strip()
        if ended is None or ended.handed_off or not text:
            raise AssistUnavailable()
        log.info("assist", extra={"conversation_id": conversation.id, "kind": kind})
        return text

    async def take_over(self, workspace_id: str, conversation_id: str, on: bool) -> Conversation:
        """The team takes the chat, and the agent stays quiet in it, or gives it back:
        the inbox's switch, "." in Business Suite or on Telegram. Quiet for
        TAKEOVER_QUIET_MINUTES, it's the agent's again."""
        async with self.sessions() as s, s.begin():
            conversation = await self._get_by_id(s, workspace_id, conversation_id)
            conversation.status = HUMAN_STATUS if on else "active"
            conversation.taken_over_at = self.now() if on else None
        log.info("taken over" if on else "given back", extra={"conversation_id": conversation.id})
        return conversation

    async def save_page_message(
        self, agent: LiveAgent, channel: str, contact: str, text: str
    ) -> None:
        """The business wrote to a buyer from its own app, not through us: Business
        Suite or Messenger. "." takes the chat over or gives it back; other words are added to
        the chat as the team's (rule 5), so the agent reads them next turn, and keep a chat
        that's taken over from going quiet."""
        async with self.sessions() as s:
            conversation = await s.scalar(
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
        if conversation is None:  # they wrote first: there's nothing to add it to yet
            return
        if text.strip() == ".":
            await self.take_over(
                agent.workspace_id, conversation.id, conversation.status != HUMAN_STATUS
            )
            return
        async with self.sessions() as s, s.begin():
            saved = await self._get_by_id(s, agent.workspace_id, conversation.id, lock=True)
            said = {**self.llm.assistant_message(text), "by": TEAM_AUTHOR}
            saved.messages = [*saved.messages, said]  # a new list, so SQLAlchemy sees it
            saved.updated_at = self.now()
            if saved.taken_over_at is not None:
                saved.taken_over_at = self.now()

    async def _return_if_quiet(self, conversation: Conversation) -> bool:
        """A chat the team took over and then left quiet is the agent's again, and so is
        one that was the team's while the AI was off for the visitor."""
        if self._team_is_answering(conversation):
            return False
        await self.take_over(conversation.workspace_id, conversation.id, False)
        conversation.status, conversation.taken_over_at = "active", None
        return True

    def _team_is_answering(self, conversation: Conversation) -> bool:
        """The team took the chat over and wrote in it less than TAKEOVER_QUIET_MINUTES ago.
        One with no time was the team's because the AI was off for the visitor: once
        it's on again, the chat is the agent's again too."""
        at = conversation.taken_over_at
        if conversation.status != HUMAN_STATUS or at is None:
            return False
        quiet = timedelta(minutes=self.settings.TAKEOVER_QUIET_MINUTES)
        return self.now() - (at if at.tzinfo else at.replace(tzinfo=UTC)) < quiet

    async def _run_turn(
        self, turn: TurnInput, conversation: Conversation, agent: LiveAgent, visitor: str
    ) -> AsyncGenerator[Event]:
        """Passes the turn's events to the listener and saves the turn when it ends.

        The listener drives this, so a tab closed mid-reply loses the turn.
        """
        async with aclosing(run_turn(turn, self.llm)) as events:
            async for event in events:
                if isinstance(event, TurnEnded):
                    await self._save(conversation, event, agent.config.handoff.auto_assign)
                    await self._alert(conversation, event)
                    if event.alerts:  # a new shop order: its buyer may get a call
                        await self.calls.after_turn(agent, conversation.id)
                    await self.visitors.after_turn(agent, visitor, event.tools)
                yield event

    async def _conversation_for_team(
        self,
        agent: LiveAgent,
        conversation: Conversation | None,
        channel: str,
        contact: str | None,
        name: str | None,
    ) -> Conversation:
        """The conversation (a new one if none) with the team answering it."""
        async with self.sessions() as s, s.begin():
            if conversation is None:
                conversation = self._new_conversation(agent, channel, contact, name)
                s.add(conversation)
            else:
                (
                    await self._get_by_id(s, agent.workspace_id, conversation.id)
                ).status = HUMAN_STATUS
            conversation.status = HUMAN_STATUS
        return conversation

    async def _save_for_team(
        self, conversation: Conversation, text: str, channel: str
    ) -> AsyncGenerator[Event]:
        """A turn with no agent: the message is saved and passed to the team."""
        message = self.llm.user_message(text)
        ended = TurnEnded([message], conversation.state, {"channel": channel, "to_team": True})
        await self._save(conversation, ended)
        await self.team.forward(conversation, text)
        yield ended

    async def _alert(self, conversation: Conversation, ended: TurnEnded) -> None:
        """Tells the owner what the turn saved, and if the visitor wants a person."""
        for alert in ended.alerts:
            await self.team.alert(conversation, alert)
        if ended.alerts:  # an order saved or changed: into a linked WooCommerce store now
            self.woo.push_soon(conversation.workspace_id)
        if ended.handed_off:
            await self.team.alert(
                conversation, handoff_alert([*conversation.messages, *ended.added])
            )

    async def _save(
        self, conversation: Conversation, ended: TurnEnded, auto_assign: bool = False
    ) -> None:
        async with self.sessions() as s, s.begin():
            saved = await self._get_by_id(s, conversation.workspace_id, conversation.id, lock=True)
            saved.messages = [*saved.messages, *ended.added]  # a new list, so SQLAlchemy sees it
            saved.state = ended.state
            saved.done_at = saved.snoozed_until = None  # they wrote again: it's back
            if ended.handed_off:
                saved.status = "needs_human"
                if auto_assign and saved.assigned_to is None:
                    saved.assigned_to = await least_busy(s, conversation.workspace_id)
            saved.updated_at = self.now()
            if not ended.trace.get("to_team"):  # a message to the team isn't an agent turn
                s.add(self._turn_row(conversation, ended))
        log.info("turn finished", extra={"conversation_id": conversation.id, "trace": ended.trace})

    def _turn_row(self, conversation: Conversation, ended: TurnEnded) -> Turn:
        """The turn's trace as a row, so the admin can add it up. No one's words."""
        trace = ended.trace
        tokens = [step["tokens"] for step in trace["steps"] if "tokens" in step]
        return Turn(
            workspace_id=conversation.workspace_id,
            conversation_id=conversation.id,
            channel=trace["channel"],
            llm=trace.get("llm", ""),
            at=self.now(),
            total_ms=trace["total_ms"],
            first_output_ms=trace["first_output_ms"],
            input_tokens=sum(t["input"] for t in tokens),
            cached_tokens=sum(t["cached"] for t in tokens),
            output_tokens=sum(t["output"] for t in tokens),
            cost_micros=trace["cost_micros"],
            tools=ended.tools,
            error=(trace["error"] or "")[:300],
            handed_off=ended.handed_off,
            interrupted=ended.interrupted,
        )

    def _new_conversation(
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

    def _active_chats_filter(self) -> tuple[ColumnElement[bool], ...]:
        """The usual list's chats: not done, and not snoozed."""
        return (
            Conversation.done_at.is_(None),
            or_(Conversation.snoozed_until.is_(None), Conversation.snoozed_until <= self.now()),
        )

    def _is_full(self, conversation: Conversation) -> bool:
        """The agent's part only: a chat kept for months isn't full."""
        turns = sum(
            1 for m in conversation.messages[conversation.history_from :] if m.get("role") == "user"
        )
        return turns >= self.settings.MAX_TURNS

    async def _get(self, s: AsyncSession, agent: LiveAgent, conversation_id: str) -> Conversation:
        conversation = await self._get_by_id(s, agent.workspace_id, conversation_id)
        if conversation.agent_id != agent.agent_id:
            raise NotFound("There's no such conversation. Start a new one.")
        return conversation

    async def by_ids(self, workspace_id: str, ids: set[str]) -> Sequence[Conversation]:
        """These of the business's conversations (the tickets' chats), in no order."""
        if not ids:
            return []
        async with self.sessions() as s:
            found = await s.scalars(
                select(Conversation).where(
                    Conversation.workspace_id == workspace_id, Conversation.id.in_(ids)
                )
            )
            return found.all()

    async def _get_by_id(
        self, s: AsyncSession, workspace_id: str, conversation_id: str, *, lock: bool = False
    ) -> Conversation:
        """`lock` before adding to `messages`: two writes at once would each add to the list
        they read, and the later one would drop the other's line. Postgres holds the row
        until the transaction ends; SQLite writes one transaction at a time anyway."""
        query = select(Conversation).where(
            Conversation.id == conversation_id, Conversation.workspace_id == workspace_id
        )
        conversation = await s.scalar(query.with_for_update() if lock else query)
        if conversation is None:
            raise NotFound("There's no such conversation. Start a new one.")
        return conversation


async def least_busy(s: AsyncSession, workspace_id: str) -> str | None:
    """The joined, available moderator with the fewest open chats (waiting for a person, or with
    the team), the longest on the team first when it's even; None with no one."""
    open_chats = (
        select(func.count())
        .select_from(Conversation)
        .where(
            Conversation.workspace_id == workspace_id,
            Conversation.assigned_to == User.id,
            Conversation.status.in_(OPEN_STATUSES),
        )
        .correlate(User)
        .scalar_subquery()
    )
    moderators = select(User.id).where(
        User.workspace_id == workspace_id,
        User.role == "moderator",
        User.email_verified_at.is_not(None),
        User.available,
    )
    return await s.scalar(moderators.order_by(open_chats, User.created_at).limit(1))
