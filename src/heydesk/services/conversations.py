"""Conversations: the one entry point for a visitor's turn, on web, voice and the chat apps
(design §6). The team's side of a chat is `services/inbox.py`."""

import asyncio
import logging
from collections.abc import AsyncGenerator, Callable
from contextlib import aclosing, suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from heydesk.config import Settings
from heydesk.errors import (
    AssistUnavailable,
    ConversationFull,
    HeyDeskError,
    NotFound,
)
from heydesk.models import Agent, Conversation, Turn
from heydesk.ports.llm import LanguageModel
from heydesk.runtime.events import Card, Event, HandOff, Photos, Token, TurnEnded
from heydesk.runtime.loop import TurnInput, run_turn
from heydesk.runtime.prompt import build_system_prompt
from heydesk.services.agents import LiveAgent
from heydesk.services.calls import CallService
from heydesk.services.inbox import InboxService, get_conversation, least_busy
from heydesk.services.knowledge import KnowledgeService
from heydesk.services.photos import PhotoService
from heydesk.services.team import HUMAN_STATUS, TEAM_AUTHOR, TeamService, handoff_alert
from heydesk.services.visitors import VisitorService, is_blocked
from heydesk.services.woo import WooService
from heydesk.shop.models import ShopOrder
from heydesk.shop.products import photo_line
from heydesk.shop.prompt import shop_note, shop_prompt
from heydesk.shop.store import ShopStore
from heydesk.shop.tools import tools_for

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
        inbox: InboxService,
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
        self.inbox = inbox
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
            saved = await get_conversation(s, conversation.workspace_id, conversation.id, lock=True)
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

    async def last_web_chat(
        self, agent: LiveAgent, visitor_id: str
    ) -> tuple[str, list[dict[str, str]]] | None:
        """This browser's latest web chat with the agent, so a reload shows it again: its id
        and the lines said, oldest first. `role` is user, assistant or team. None: no chat yet."""
        async with self.sessions() as s:
            conversation = await s.scalar(
                select(Conversation)
                .where(
                    Conversation.workspace_id == agent.workspace_id,
                    Conversation.agent_id == agent.agent_id,
                    Conversation.channel == "web",
                    Conversation.contact == visitor_id,
                )
                .order_by(Conversation.updated_at.desc())
                .limit(1)
            )
        if conversation is None:
            return None
        lines = [
            {"role": TEAM_AUTHOR if m.get("by") == TEAM_AUTHOR else m["role"], "text": m["content"]}
            for m in conversation.messages
            # tool calls and their results aren't lines anyone said
            if m.get("role") in ("user", "assistant")
            and isinstance(m.get("content"), str)
            and m["content"].strip()
        ]
        return conversation.id, lines

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

    async def run(self, every: float) -> None:
        """Brings snoozed chats back when they're due, and nudges unfinished orders,
        every `every` seconds, until cancelled (the app's lifespan starts it)."""
        while True:
            await asyncio.sleep(every)
            try:
                await self.inbox.wake_snoozed()
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

    async def assist(
        self, agent: LiveAgent, conversation_id: str, kind: str, question: str = ""
    ) -> str:
        """The model's help for the team on a chat: a summary, an answer about it, or a
        draft of the next reply. The chat's own prompt and history, no tools, and one line of
        what's wanted; the words come back to the team and nothing is added to the chat or
        sent. Traced and billed as a turn, on the channel "assist"."""
        workspace_id = agent.workspace_id
        async with self.sessions() as s:
            conversation = await get_conversation(s, workspace_id, conversation_id)
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
            await self.inbox.take_over(
                agent.workspace_id, conversation.id, conversation.status != HUMAN_STATUS
            )
            return
        async with self.sessions() as s, s.begin():
            saved = await get_conversation(s, agent.workspace_id, conversation.id, lock=True)
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
        await self.inbox.take_over(conversation.workspace_id, conversation.id, False)
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
                    await get_conversation(s, agent.workspace_id, conversation.id)
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
            saved = await get_conversation(s, conversation.workspace_id, conversation.id, lock=True)
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

    def _is_full(self, conversation: Conversation) -> bool:
        """The agent's part only: a chat kept for months isn't full."""
        turns = sum(
            1 for m in conversation.messages[conversation.history_from :] if m.get("role") == "user"
        )
        return turns >= self.settings.MAX_TURNS

    async def _get(self, s: AsyncSession, agent: LiveAgent, conversation_id: str) -> Conversation:
        conversation = await get_conversation(s, agent.workspace_id, conversation_id)
        if conversation.agent_id != agent.agent_id:
            raise NotFound("There's no such conversation. Start a new one.")
        return conversation
