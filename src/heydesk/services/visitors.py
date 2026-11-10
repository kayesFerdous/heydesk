"""Limits on a public chat, so visitors here for timepass can't spend a business's turns,
and the team's say over one visitor: Block them, or turn the AI off for them.

Before anything, `let_in` refuses a blocked visitor (telling them once) and says when the AI is
off for them. A messaging app's every message goes through `arrived`, which blocks a flood.
Before a turn, `admit` counts it, or refuses when the visitor is blocked, the business's plan
has ended or used its replies, the visitor used today's turns, or the whole business did. A refusal costs no model call. After a turn,
`after_turn` gives a strike when the model called `off_topic`; enough strikes in a row block the
visitor for a while. Any real step (another tool) clears the strikes.
"""

import logging
from collections.abc import Callable
from contextlib import suppress
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from limits import parse
from limits.aio.storage import MemoryStorage
from limits.aio.strategies import SlidingWindowCounterRateLimiter
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from heydesk.config import Settings
from heydesk.errors import NotFound, VisitorLimited, VisitorSilenced
from heydesk.models import Visitor
from heydesk.services.agents import LiveAgent
from heydesk.services.billing import plan_problem
from heydesk.shop.tools.inbox import OFF_TOPIC

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
        self.flood = SlidingWindowCounterRateLimiter(MemoryStorage())  # in memory

    async def check_not_blocked(self, agent: LiveAgent, key: str) -> bool:
        """Raises when they're blocked: `VisitorLimited` the first time, with how to reach
        the business, and `VisitorSilenced` after, so a messaging app tells them once. False
        when the team turned the AI off for them: their message goes to the team."""
        now = self.now()
        async with self.sessions() as s, s.begin():
            visitor = await self._find(s, agent.workspace_id, key)
            if visitor is None:
                return True
            blocked, told, ai_off = is_blocked(visitor, now), visitor.told_blocked, visitor.ai_off
            if blocked:
                visitor.told_blocked = True
        if blocked:
            log.info("visitor blocked", extra={"workspace_id": agent.workspace_id})
            raise (VisitorSilenced if told else VisitorLimited)(limited_message(agent))
        return not ai_off

    async def count_message(self, workspace_id: str, key: str) -> None:
        """One message from a messaging app. `FLOOD_MESSAGES` in a minute block them for
        `BLOCK_HOURS`, as chatting off topic does. The web chat has its own limit."""
        limit = parse(f"{self.settings.FLOOD_MESSAGES}/minute")
        if await self.flood.hit(limit, "flood", workspace_id, key):
            return
        await self._first_seen(
            workspace_id, key, self.now().date()
        )  # admit puts the business's day right
        async with self.sessions() as s, s.begin():
            visitor = await self._find(s, workspace_id, key, lock=True)
            assert visitor is not None
            if not is_blocked(visitor, self.now()):
                until = self.now() + timedelta(hours=self.settings.BLOCK_HOURS)
                block(visitor, "flooding", until)
                log.info("visitor flooding", extra={"workspace_id": workspace_id})

    async def find(self, workspace_id: str, key: str) -> Visitor | None:
        async with self.sessions() as s:
            return await self._find(s, workspace_id, key)

    async def change(
        self, workspace_id: str, visitor_id: str, by: str, blocked: bool | None, ai_off: bool | None
    ) -> Visitor:
        """The team blocks or unblocks a visitor, or turns the AI off or on for them.
        `by` is who did it (their email). A block by the team lasts until someone unblocks."""
        async with self.sessions() as s, s.begin():
            visitor = await s.scalar(
                select(Visitor).where(
                    Visitor.id == visitor_id, Visitor.workspace_id == workspace_id
                )
            )
            if visitor is None:
                raise NotFound("There's no such visitor.")
            if blocked is True:
                block(visitor, "team", None, by)
            elif blocked is False:
                block(visitor, None, None)
                visitor.strikes = 0
            if ai_off is not None:
                visitor.ai_off = ai_off
            visitor.updated_at = self.now()
        log.info("visitor changed", extra={"workspace_id": workspace_id, "visitor_id": visitor_id})
        return visitor

    async def blocked(self, workspace_id: str) -> list[Visitor]:
        """Who is blocked now, the newest block first."""
        now = self.now()
        async with self.sessions() as s:
            found = await s.scalars(
                select(Visitor)
                .where(Visitor.workspace_id == workspace_id, Visitor.blocked_why.is_not(None))
                .order_by(Visitor.updated_at.desc())
            )
            return [v for v in found if is_blocked(v, now)]

    async def count_turn(self, agent: LiveAgent, key: str, *, count: bool = True) -> None:
        """Counts one turn for the visitor, or raises `VisitorLimited`. With `count=False` it
        only checks: a voice call checks before it connects."""
        now = self.now()
        today = now.astimezone(ZoneInfo(agent.config.timezone)).date()
        await self._first_seen(agent.workspace_id, key, today)
        async with self.sessions() as s, s.begin():
            visitor = await self._find(s, agent.workspace_id, key, lock=True)  # counted once each
            assert visitor is not None
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
                    until = self.now() + timedelta(hours=self.settings.BLOCK_HOURS)
                    block(visitor, "off_topic", until)
                    visitor.strikes = 0
                    log.info("visitor blocked", extra={"workspace_id": agent.workspace_id})
            visitor.updated_at = self.now()

    async def _limit(
        self, s: AsyncSession, workspace_id: str, visitor: Visitor, today: date, now: datetime
    ) -> str | None:
        """Why the visitor can't have a turn now, or None."""
        if is_blocked(visitor, now):
            return "blocked"
        plan = await plan_problem(s, workspace_id, now)  # ended, or its replies used up
        if plan:
            return plan
        if visitor.turns >= self.settings.VISITOR_DAILY_TURNS:
            return "visitor_daily_turns"
        business_turns = await s.scalar(
            select(func.coalesce(func.sum(Visitor.turns), 0)).where(
                Visitor.workspace_id == workspace_id, Visitor.day == today
            )
        )
        if (business_turns or 0) >= self.settings.WORKSPACE_DAILY_TURNS:
            return "workspace_daily_turns"
        return None

    async def _first_seen(self, workspace_id: str, key: str, today: date) -> None:
        """Makes the visitor's row, the first time they write. Two first messages at once:
        the unique (workspace, key) refuses the second row, and both go on with the first."""
        async with self.sessions() as s:
            if await self._find(s, workspace_id, key) is not None:
                return
        with suppress(IntegrityError):
            async with self.sessions() as s, s.begin():
                s.add(Visitor(workspace_id=workspace_id, key=key, day=today, turns=0))

    async def _find(
        self, s: AsyncSession, workspace_id: str, key: str, *, lock: bool = False
    ) -> Visitor | None:
        """`lock` before changing it: two turns at once would both count from the same number."""
        query = select(Visitor).where(Visitor.workspace_id == workspace_id, Visitor.key == key)
        return await s.scalar(query.with_for_update() if lock else query)


def visitor_key(channel: str, contact: str) -> str:
    """Who a conversation's visitor is: `web:<token>` for the web chat and a call (they share
    the browser's token), `<app>:<chat id>` on a messaging app."""
    return f"{'web' if channel in ('web', 'voice') else channel}:{contact}"


def is_blocked(visitor: Visitor, now: datetime) -> bool:
    """Blocked for a reason, and until a time still to come or until someone unblocks them."""
    return visitor.blocked_why is not None and (
        visitor.blocked_until is None or visitor.blocked_until > now
    )


def block(visitor: Visitor, why: str | None, until: datetime | None, by: str | None = None) -> None:
    """Blocks the visitor (`why` None: unblocks), so their next message gets the one notice."""
    visitor.blocked_why, visitor.blocked_until, visitor.blocked_by = why, until, by
    visitor.told_blocked = False


def limited_message(agent: LiveAgent) -> str:
    """What a limited visitor reads (or hears): how to reach the business another way."""
    contact = agent.config.handoff
    reach = contact.phone or contact.email
    text = "Sorry, this chat can't take more messages right now."
    return f"{text} Please contact us at {reach}." if reach else text
