"""Conversations: the one entry point for web and voice (design §6)."""

import asyncio
import logging
from collections.abc import AsyncGenerator, Callable
from contextlib import aclosing
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from heydesk.config import Settings
from heydesk.desks import tools_for
from heydesk.errors import ConversationFull, HeyDeskError, NotFound
from heydesk.models import Conversation
from heydesk.runtime.events import Event, HandOff, Token, TurnEnded
from heydesk.runtime.loop import Runtime, TurnInput, run_turn
from heydesk.runtime.prompt import build_system_prompt
from heydesk.services.agents import LiveAgent
from heydesk.services.knowledge import KnowledgeService
from heydesk.services.team import HUMAN, TEAM, TeamService, handoff_alert

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


class ConversationService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        runtime: Runtime,
        knowledge: KnowledgeService,
        team: TeamService,
        settings: Settings,
        now: Callable[[], datetime],
    ) -> None:
        self.sessions = sessions
        self.runtime = runtime
        self.knowledge = knowledge
        self.team = team
        self.settings = settings
        self.now = now

    async def start_turn(
        self,
        agent: LiveAgent,
        conversation_id: str | None,
        text: str,
        channel: str,
        contact: str | None = None,
    ) -> RunningTurn:
        """Loads the conversation (or starts one), then runs the turn as the listener reads it.
        A messaging app passes its `contact` (the chat id) for a new conversation."""
        text = text.strip()
        if not text or len(text) > self.settings.MAX_MESSAGE_CHARS:
            raise HeyDeskError(f"A message is 1 to {self.settings.MAX_MESSAGE_CHARS} characters.")
        async with self.sessions() as s, s.begin():
            if conversation_id:
                conv = await self._get(s, agent, conversation_id)
                if self._is_full(conv):
                    raise ConversationFull(
                        "This conversation is very long. Please start a new one."
                    )
            else:
                conv = self._new(agent, channel, contact)
                s.add(conv)
        if conv.status == HUMAN:  # the team is answering: the agent stays quiet (D43)
            return RunningTurn(conv.id, self._to_team(conv, text, channel), asyncio.Event())

        now = self.now()
        desk, config = agent.desk, agent.config
        store = desk.store(
            self.sessions,
            agent.workspace_id,
            self.now,
            conversation_id=conv.id,
            knowledge=self.knowledge,
        )
        turn = TurnInput(
            system=build_system_prompt(
                config, await desk.prompt(config, store), channel, now, agent.documents
            ),
            tools=tools_for(desk, has_documents=bool(agent.documents)),
            model=self.settings.MODEL,
            history=[{k: v for k, v in m.items() if k != "by"} for m in conv.messages],
            user_text=text,
            channel=channel,
            agent=config,
            store=store,
            now=now,
            state=conv.state,
            note=lambda state: desk.note(config, state),
            is_first=not conv.messages,
        )
        return RunningTurn(conv.id, self._run(turn, conv), turn.interrupt)

    async def reply(self, agent: LiveAgent, channel: str, contact: str, text: str) -> Reply:
        """For a messaging app (D42): continues the contact's conversation, or starts one, and
        returns the whole reply. A conversation that is full, or quiet for
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
        turn = await self.start_turn(agent, latest.id if latest else None, text, channel, contact)
        parts: list[str] = []
        handoff = None
        async with aclosing(turn.events) as events:
            async for event in events:
                if isinstance(event, Token):
                    parts.append(event.text)
                elif isinstance(event, HandOff):
                    handoff = event
        return Reply("".join(parts).strip(), handoff)

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

    async def list(self, ws: str, status: str | None = None) -> list[Conversation]:
        """Newest activity first."""
        query = (
            select(Conversation)
            .where(Conversation.workspace_id == ws)
            .order_by(Conversation.updated_at.desc())
        )
        if status:
            query = query.where(Conversation.status == status)
        async with self.sessions() as s:
            return list(await s.scalars(query))

    async def get(self, ws: str, conversation_id: str) -> Conversation:
        async with self.sessions() as s:
            return await self._get_by_id(s, ws, conversation_id)

    async def _run(self, turn: TurnInput, conv: Conversation) -> AsyncGenerator[Event]:
        """Passes the turn's events to the listener and saves the turn when it ends.

        The listener drives this, so a tab closed mid-reply loses the turn (D3).
        """
        async with aclosing(run_turn(turn, self.runtime)) as events:
            async for event in events:
                if isinstance(event, TurnEnded):
                    await self._save(conv, event)
                    await self._alert(conv, event)
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
        log.info("turn finished", extra={"conversation_id": conv.id, "trace": ended.trace})

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
