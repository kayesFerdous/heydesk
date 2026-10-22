"""Conversations: the one entry point for web and voice (design §6)."""

import asyncio
import logging
from collections.abc import AsyncGenerator, Callable
from contextlib import aclosing
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from heydesk.config import Settings
from heydesk.desks import tools_for
from heydesk.errors import (
    ConversationFull,
    HeyDeskError,
    InvalidTransition,
    NotFound,
    UnreadablePhoto,
)
from heydesk.models import Conversation, Turn
from heydesk.runtime.events import Event, HandOff, Photos, Token, TurnEnded
from heydesk.runtime.loop import Runtime, TurnInput, run_turn
from heydesk.runtime.prompt import build_system_prompt
from heydesk.services.agents import LiveAgent
from heydesk.services.calls import CallService
from heydesk.services.knowledge import KnowledgeService
from heydesk.services.photos import PhotoService
from heydesk.services.team import HUMAN, TEAM, TeamService, handoff_alert
from heydesk.services.visitors import VisitorService
from heydesk.services.woo import WooService

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
    handoff: HandOff | None
    photos: list[dict[str, str]] = field(default_factory=list)  # shown by a tool (D62)
    choices: list[str] = field(default_factory=list)  # buttons under the text (D99)


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
    ) -> RunningTurn:
        """Loads the conversation (or starts one), then runs the turn as the listener reads it.
        `visitor` is who is talking, for the limits (D51): `web:<token>`, `telegram:<chat id>`.
        A messaging app passes its `contact` (the chat id) for a new conversation. A web visitor
        can send a `photo` with it, or instead of words, when the desk takes photos (D57)."""
        text = text.strip()
        if photo is not None and not agent.desk.takes_photos:
            raise UnreadablePhoto("This chat can't take photos.")
        if not (text or photo) or len(text) > self.settings.MAX_MESSAGE_CHARS:
            raise HeyDeskError(f"A message is 1 to {self.settings.MAX_MESSAGE_CHARS} characters.")
        # Read before anything is saved, so a file that isn't a photo changes nothing
        saved = await self.photos.save(agent.workspace_id, "chat", photo) if photo else None
        conv = None
        if conversation_id:
            async with self.sessions() as s:
                conv = await self._get(s, agent, conversation_id)
            if self._is_full(conv):
                raise ConversationFull("This conversation is very long. Please start a new one.")
            if conv.status == HUMAN:  # the team is answering: the agent stays quiet (D43)
                return RunningTurn(conv.id, self._to_team(conv, text, channel), asyncio.Event())
        await self.visitors.admit(agent, visitor)  # counts the turn, or refuses it (D51)
        if conv is None:
            conv = self._new(agent, channel, contact)
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
        self, agent: LiveAgent, channel: str, contact: str, text: str, photo: bytes | None = None
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
        )
        parts: list[str] = []
        photos: list[dict[str, str]] = []
        handoff = None
        choices: list[str] = []
        async with aclosing(turn.events) as events:
            async for event in events:
                if isinstance(event, Token):
                    parts.append(event.text)
                elif isinstance(event, HandOff):
                    handoff = event
                elif isinstance(event, Photos):
                    photos.extend(event.items)
                elif isinstance(event, TurnEnded):
                    choices = event.choices
        return Reply("".join(parts).strip(), handoff, photos, choices)

    async def restart(self, agent: LiveAgent, channel: str, contact: str) -> None:
        """A new, empty conversation for the contact (Telegram's /start); their next message
        goes into it, after the greeting."""
        async with self.sessions() as s, s.begin():
            s.add(self._new(agent, channel, contact))

    async def team_replies(self, agent: LiveAgent, conversation_id: str) -> list[str]:
        """What the team has written in a web chat, oldest first; the page shows the new ones."""
        async with self.sessions() as s:
            conv = await self._get(s, agent, conversation_id)
        return [m["content"] for m in conv.messages if m.get("by") == TEAM]

    async def list(
        self,
        ws: str,
        status: str | None = None,
        *,
        limit: int = 100,
        before: datetime | None = None,
    ) -> list[Conversation]:
        """Newest activity first, a page at a time: the next page is `before=` the last
        one's `updated_at`."""
        query = (
            select(Conversation)
            .where(Conversation.workspace_id == ws)
            .order_by(Conversation.updated_at.desc())
            .limit(limit)
        )
        if status:
            query = query.where(Conversation.status == status)
        if before:
            query = query.where(Conversation.updated_at < before)
        async with self.sessions() as s:
            return list(await s.scalars(query))

    async def get(self, ws: str, conversation_id: str) -> Conversation:
        async with self.sessions() as s:
            return await self._get_by_id(s, ws, conversation_id)

    async def hand_back(self, ws: str, conversation_id: str) -> Conversation:
        """The owner dealt with a hand-off from the console: the agent answers again, as
        after `/done` on Telegram (D83)."""
        async with self.sessions() as s, s.begin():
            conv = await self._get_by_id(s, ws, conversation_id)
            if conv.status == "active":
                raise InvalidTransition("This conversation isn't waiting for a person.")
            conv.status = "active"
        log.info("hand-off handled", extra={"conversation_id": conv.id})
        return conv

    async def _run(
        self, turn: TurnInput, conv: Conversation, agent: LiveAgent, visitor: str
    ) -> AsyncGenerator[Event]:
        """Passes the turn's events to the listener and saves the turn when it ends.

        The listener drives this, so a tab closed mid-reply loses the turn (D3).
        """
        async with aclosing(run_turn(turn, self.runtime)) as events:
            async for event in events:
                if isinstance(event, TurnEnded):
                    await self._save(conv, event)
                    await self._alert(conv, event)
                    if event.alerts:  # a new shop order: its buyer may get a call (D89)
                        await self.calls.after_turn(agent, conv.id)
                    await self.visitors.after_turn(agent, visitor, event.tools)
                yield event

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

    async def _save(self, conv: Conversation, ended: TurnEnded) -> None:
        async with self.sessions() as s, s.begin():
            saved = await self._get_by_id(s, conv.workspace_id, conv.id)
            saved.messages = [*saved.messages, *ended.added]  # a new list, so SQLAlchemy sees it
            saved.state = ended.state
            if ended.handed_off:
                saved.status = "needs_human"
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

    def _new(self, agent: LiveAgent, channel: str, contact: str | None) -> Conversation:
        return Conversation(
            workspace_id=agent.workspace_id,
            agent_id=agent.agent_id,
            channel=channel,
            contact=contact,
            state={},
            messages=[],
            created_at=self.now(),
            updated_at=self.now(),
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
