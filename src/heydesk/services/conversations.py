"""Conversations: the one entry point for a visitor's turn, on web, voice and the chat apps
(design §6). The team's side of a chat is `services/inbox.py`."""

import asyncio
import logging
from collections.abc import AsyncGenerator, Callable
from contextlib import aclosing
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from heydesk.config import Settings
from heydesk.errors import (
    ConversationFull,
    HeyDeskError,
    NotFound,
)
from heydesk.models import Conversation, Turn
from heydesk.ports.llm import LanguageModel
from heydesk.runtime.events import Card, Event, HandedOff, Photos, Token, TurnEnded
from heydesk.runtime.loop import TurnInput, run_turn
from heydesk.runtime.prompt import build_system_prompt
from heydesk.runtime.trace import summary
from heydesk.services.agents import AgentService, LiveAgent
from heydesk.services.calls import ConfirmationCallService
from heydesk.services.inbox import InboxService, get_conversation, least_busy
from heydesk.services.photos import PhotoService
from heydesk.services.team import (
    HUMAN_STATUS,
    TEAM_AUTHOR,
    TeamService,
    add_team_line,
    handoff_alert,
)
from heydesk.services.visitors import VisitorService, is_blocked
from heydesk.services.woo import WooService
from heydesk.shop.products import photo_line
from heydesk.shop.prompt import shop_note, shop_prompt
from heydesk.shop.tools import tools_for

log = logging.getLogger(__name__)


@dataclass
class RunningTurn:
    conversation_id: str
    events: AsyncGenerator[Event]  # the listener reads these; the turn is saved at the end
    interrupt: asyncio.Event  # set it to stop the turn at the next token (a barge-in)


@dataclass(frozen=True)
class Reply:
    """A whole turn's reply, for a channel that sends messages, not a stream (Telegram)."""

    text: str  # empty when the turn failed: the hand-off says what to do
    handoff: HandedOff | None
    photos: list[dict[str, str]] = field(default_factory=list)  # shown by a tool
    choices: list[str] = field(default_factory=list)  # buttons under the text
    card: Card | None = None  # the last card a tool showed, sent its own way
    conversation_id: str = ""  # a product card's page links back to it


def turn_row(conversation: Conversation, ended: TurnEnded, at: datetime) -> Turn:
    """The turn's trace as a row, so the admin can add it up. No one's words."""
    trace = ended.trace
    tokens = [step["tokens"] for step in trace["steps"] if "tokens" in step]
    return Turn(
        workspace_id=conversation.workspace_id,
        conversation_id=conversation.id,
        channel=trace["channel"],
        llm=trace.get("llm", ""),
        at=at,
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


class ConversationService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        llm: LanguageModel,
        agents: AgentService,
        photos: PhotoService,
        team: TeamService,
        inbox: InboxService,
        visitors: VisitorService,
        woo: WooService,
        calls: ConfirmationCallService,
        settings: Settings,
        now: Callable[[], datetime],
    ) -> None:
        self.sessions = sessions
        self.llm = llm
        self.agents = agents  # builds each turn's store
        self.photos = photos  # a visitor's photo: saved, and matched to the products
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
        visitor_key: str,
        contact: str | None = None,
        photo: bytes | None = None,
        name: str | None = None,
    ) -> RunningTurn:
        """Loads the conversation (or starts one), then runs the turn as the listener reads it.
        `visitor_key` is who is talking, for the limits: `web:<token>`, `telegram:<chat id>`.
        A messaging app passes its `contact` (the chat id) and their `name`, if it knows it, for a
        new conversation; the web chat and a call pass the browser's token. A web visitor
        can send a `photo` with it, or instead of words."""
        text = text.strip()
        if not (text or photo) or len(text) > self.settings.MAX_MESSAGE_CHARS:
            raise HeyDeskError(f"A message is 1 to {self.settings.MAX_MESSAGE_CHARS} characters.")
        runs_ai = await self.visitors.ai_may_answer(
            agent, visitor_key
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
            return self._team_turn(conversation, text, channel)
        # the team is answering: the agent stays quiet, until it's quiet
        if (
            conversation is not None
            and conversation.status == HUMAN_STATUS
            and not await self._return_if_quiet(conversation)
        ):
            return self._team_turn(conversation, text, channel)
        await self.visitors.count_turn(agent, visitor_key)  # counts the turn, or refuses it
        if conversation is None:
            conversation = self._new_conversation(agent, channel, contact, name)
            async with self.sessions() as s, s.begin():
                s.add(conversation)

        now = self.now()
        config = agent.config
        store = self.agents.build_store(agent.workspace_id, conversation.id)
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
            conversation.id, self._run_turn(turn, conversation, agent, visitor_key), turn.interrupt
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
                elif isinstance(event, HandedOff):
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
        visitor_key = f"{channel}:{contact}"
        found = await self.visitors.find(agent.workspace_id, visitor_key)
        if found is not None and (found.ai_off or is_blocked(found, self.now())):
            return False
        if agent.config.ai_off:
            return False
        latest = await self._latest_conversation(agent, channel, contact)
        return latest is None or not self._team_is_answering(latest)

    async def _latest_conversation(
        self, agent: LiveAgent, channel: str, contact: str
    ) -> Conversation | None:
        """The contact's conversation on a channel, or None. On a messaging app they keep
        one: the newest, for chats split before that. On the web the contact is the browser's
        token, and the newest is the chat a reload shows again."""
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
        return self._is_full(conversation) or self.now() - conversation.updated_at > idle

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
        conversation = await self._latest_conversation(agent, "web", visitor_id)
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

    async def save_page_message(
        self, agent: LiveAgent, channel: str, contact: str, text: str
    ) -> None:
        """The business wrote to a buyer from its own app, not through us: Business
        Suite or Messenger. "." takes the chat over or gives it back; other words are added to
        the chat as the team's (rule 5), so the agent reads them next turn, and keep a chat
        that's taken over from going quiet."""
        conversation = await self._latest_conversation(agent, channel, contact)
        if conversation is None:  # they wrote first: there's nothing to add it to yet
            return
        if text.strip() == ".":
            await self.inbox.set_taken_over(
                agent.workspace_id, conversation.id, conversation.status != HUMAN_STATUS
            )
            return
        async with self.sessions() as s, s.begin():
            saved = await get_conversation(s, agent.workspace_id, conversation.id, lock=True)
            add_team_line(saved, self.llm, text, self.now())
            if saved.taken_over_at is not None:
                saved.taken_over_at = self.now()

    async def _return_if_quiet(self, conversation: Conversation) -> bool:
        """A chat the team took over and then left quiet is the agent's again, and so is
        one that was the team's while the AI was off for the visitor."""
        if self._team_is_answering(conversation):
            return False
        await self.inbox.set_taken_over(conversation.workspace_id, conversation.id, False)
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
        return self.now() - at < quiet

    async def _run_turn(
        self, turn: TurnInput, conversation: Conversation, agent: LiveAgent, visitor_key: str
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
                        await self.calls.queue_new_orders(agent, conversation.id)
                    await self.visitors.count_off_topic_strike(agent, visitor_key, event.tools)
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

    def _team_turn(self, conversation: Conversation, text: str, channel: str) -> RunningTurn:
        """A turn the agent stays out of: nothing to interrupt, and no model runs."""
        return RunningTurn(
            conversation.id, self._save_for_team(conversation, text, channel), asyncio.Event()
        )

    async def _save_for_team(
        self, conversation: Conversation, text: str, channel: str
    ) -> AsyncGenerator[Event]:
        """A turn with no agent: the message is saved and passed to the team."""
        message = self.llm.user_message(text)
        ended = TurnEnded([message], conversation.state, {"channel": channel, "to_team": True})
        await self._save(conversation, ended)
        await self.team.forward_to_team(conversation, text)
        yield ended

    async def _alert(self, conversation: Conversation, ended: TurnEnded) -> None:
        """Tells the owner what the turn saved, and if the visitor wants a person."""
        for alert in ended.alerts:
            await self.team.alert_team(conversation, alert)
        if ended.alerts:  # an order saved or changed: into a linked WooCommerce store now
            self.woo.push_soon(conversation.workspace_id)
        if ended.handed_off:
            await self.team.alert_team(
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
                s.add(turn_row(conversation, ended, self.now()))
        extra = {"conversation_id": conversation.id, "trace": ended.trace}
        if ended.trace.get("to_team"):
            log.info("message saved for the team", extra=extra)
        elif ended.trace["error"]:  # the visitor got the polite line and the hand-off
            log.warning("turn failed: %s", summary(ended.trace), extra=extra)
        else:
            log.info("turn finished: %s", summary(ended.trace), extra=extra)

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
