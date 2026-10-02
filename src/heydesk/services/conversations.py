"""Conversations: the one entry point for web and voice (design §6)."""

import asyncio
import logging
from collections.abc import AsyncGenerator, Callable
from contextlib import aclosing
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from heydesk.config import Settings
from heydesk.errors import HeyDeskError, NotFound
from heydesk.models import Conversation
from heydesk.runtime.events import Event, TurnEnded
from heydesk.runtime.loop import Runtime, TurnInput, run_turn
from heydesk.runtime.prompt import build_system_prompt
from heydesk.runtime.tool import Tool
from heydesk.services.agents import LiveAgent
from heydesk.services.items import ItemService
from heydesk.services.orders import OrderService
from heydesk.services.requests import RequestService
from heydesk.tools import TOOLS

log = logging.getLogger(__name__)


def enabled_tools(agent: LiveAgent) -> list[Tool]:
    """The agent's tools that are built. `search_items` only when the items aren't all listed."""
    names = [n for n in agent.config.tools if n in TOOLS]
    if agent.items_in_prompt:
        names = [n for n in names if n != "search_items"]
    return [TOOLS[n] for n in names]


@dataclass
class RunningTurn:
    conversation_id: str
    events: AsyncGenerator[Event]  # the listener reads these; the turn is saved at the end
    interrupt: asyncio.Event  # set it to stop the turn at the next token (a barge-in)


class ConversationService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        runtime: Runtime,
        items: ItemService,
        orders: OrderService,
        requests: RequestService,
        settings: Settings,
        now: Callable[[], datetime],
    ) -> None:
        self.sessions = sessions
        self.runtime = runtime
        self.items = items
        self.orders = orders
        self.requests = requests
        self.settings = settings
        self.now = now

    async def start_turn(
        self, agent: LiveAgent, conversation_id: str | None, text: str, channel: str
    ) -> RunningTurn:
        """Loads the conversation (or starts one), then runs the turn as the listener reads it."""
        text = text.strip()
        if not text or len(text) > self.settings.MAX_MESSAGE_CHARS:
            raise HeyDeskError(f"A message is 1 to {self.settings.MAX_MESSAGE_CHARS} characters.")
        async with self.sessions() as s, s.begin():
            if conversation_id:
                conv = await self._get(s, agent, conversation_id)
            else:
                conv = Conversation(
                    workspace_id=agent.workspace_id,
                    agent_id=agent.agent_id,
                    channel=channel,
                    state={},
                    messages=[],
                    created_at=self.now(),
                    updated_at=self.now(),
                )
                s.add(conv)

        now = self.now()
        turn = TurnInput(
            system=build_system_prompt(agent.config, agent.tasks, agent.knowledge, channel, now),
            tools=enabled_tools(agent),
            model=self.settings.MODEL,
            history=conv.messages,
            user_text=text,
            channel=channel,
            agent=agent.config,
            items=self.items.catalog(agent.workspace_id, agent.agent_id),
            orders=self.orders.for_conversation(agent.workspace_id, agent.agent_id, conv.id),
            requests=self.requests.for_conversation(agent.workspace_id, agent.agent_id, conv.id),
            tasks=agent.tasks,
            now=now,
            state=conv.state,
            is_first=not conv.messages,
        )
        return RunningTurn(conv.id, self._run(turn, conv), turn.interrupt)

    async def list(
        self, ws: str, agent_id: str | None = None, status: str | None = None
    ) -> list[Conversation]:
        """Newest activity first."""
        query = (
            select(Conversation)
            .where(Conversation.workspace_id == ws)
            .order_by(Conversation.updated_at.desc())
        )
        if agent_id:
            query = query.where(Conversation.agent_id == agent_id)
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
                yield event

    async def _save(self, conv: Conversation, ended: TurnEnded) -> None:
        async with self.sessions() as s, s.begin():
            saved = await self._get_by_id(s, conv.workspace_id, conv.id)
            saved.messages = [*saved.messages, *ended.added]  # a new list, so SQLAlchemy sees it
            saved.state = ended.state
            if ended.handed_off:
                saved.status = "needs_human"
            saved.updated_at = self.now()
        log.info("turn finished", extra={"conversation_id": conv.id, "trace": ended.trace})

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
