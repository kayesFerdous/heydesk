"""Limits on a public chat (D51), so visitors here for timepass can't spend a business's turns.

Before a turn, `admit` counts it, or refuses when the visitor is blocked, the visitor used
today's turns, or the whole business did. A refusal costs no model call. After a turn,
`after_turn` gives a strike when the model called `off_topic`; enough strikes in a row block the
visitor for a while. Any real step (another tool) clears the strikes.
"""

import logging
from collections.abc import Callable
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from heydesk.config import Settings
from heydesk.desks.shared import OFF_TOPIC
from heydesk.errors import VisitorLimited
from heydesk.models import Visitor
from heydesk.services.agents import LiveAgent

log = logging.getLogger(__name__)


class VisitorService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        settings: Settings,
        now: Callable[[], datetime],
    ) -> None:
        self.sessions = sessions
        self.settings = settings
        self.now = now

    async def admit(self, agent: LiveAgent, key: str, *, count: bool = True) -> None:
        """Counts one turn for the visitor, or raises `VisitorLimited`. With `count=False` it
        only checks: a voice call checks before it connects."""
        now = self.now()
        today = now.astimezone(ZoneInfo(agent.config.timezone)).date()
        async with self.sessions() as s, s.begin():
            visitor = await self._find(s, agent.workspace_id, key)
            if visitor is None:
                visitor = Visitor(workspace_id=agent.workspace_id, key=key, day=today, turns=0)
                s.add(visitor)
            if visitor.day != today:  # a new day: yesterday's turns don't count
                visitor.day, visitor.turns = today, 0
            reason = await self._limit(s, agent.workspace_id, visitor, today, now)
            if reason:
                log.info(
                    "visitor limited", extra={"workspace_id": agent.workspace_id, "why": reason}
                )
                raise VisitorLimited(limited_message(agent))
            if count:
                visitor.turns += 1
                visitor.updated_at = now

    async def after_turn(self, agent: LiveAgent, key: str, tools: list[str]) -> None:
        """A strike for an off-topic turn; another tool means real progress and clears them."""
        if not tools:
            return  # a plain answer: neither progress nor timepass
        async with self.sessions() as s, s.begin():
            visitor = await self._find(s, agent.workspace_id, key)
            if visitor is None:
                return
            if any(name != OFF_TOPIC.name for name in tools):
                visitor.strikes = 0
            else:
                visitor.strikes += 1
                if visitor.strikes >= self.settings.OFF_TOPIC_STRIKES:
                    visitor.blocked_until = self.now() + timedelta(hours=self.settings.BLOCK_HOURS)
                    visitor.strikes = 0
                    log.info("visitor blocked", extra={"workspace_id": agent.workspace_id})
            visitor.updated_at = self.now()

    async def _limit(
        self, s: AsyncSession, ws: str, visitor: Visitor, today: date, now: datetime
    ) -> str | None:
        """Why the visitor can't have a turn now, or None."""
        if visitor.blocked_until and visitor.blocked_until > now:
            return "blocked"
        if visitor.turns >= self.settings.VISITOR_DAILY_TURNS:
            return "visitor_daily_turns"
        business_turns = await s.scalar(
            select(func.coalesce(func.sum(Visitor.turns), 0)).where(
                Visitor.workspace_id == ws, Visitor.day == today
            )
        )
        if (business_turns or 0) >= self.settings.WORKSPACE_DAILY_TURNS:
            return "workspace_daily_turns"
        return None

    async def _find(self, s: AsyncSession, ws: str, key: str) -> Visitor | None:
        return await s.scalar(select(Visitor).where(Visitor.workspace_id == ws, Visitor.key == key))


def limited_message(agent: LiveAgent) -> str:
    """What a limited visitor reads (or hears): how to reach the business another way."""
    contact = agent.config.handoff
    reach = contact.phone or contact.email
    text = "Sorry, this chat can't take more messages right now."
    return f"{text} Please contact us at {reach}." if reach else text
