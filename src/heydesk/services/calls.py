"""Calls to confirm shop orders: the queue, and the `/phone` pages that ring.

A shop that turned calls on gets a `calls` row for each new order. `run` checks the queue
every CALL_QUEUE_SECONDS and rings the next call due on the buyer's `/phone` page: a web page
on a real phone that stands in for one (there is no phone carrier). Answer starts the usual
voice call (`voice/call.py`) in a new conversation that already has the call's first line.

The pages are kept in memory, like a phone's own state; the queue is a table.
"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from heydesk.config import Settings
from heydesk.errors import Conflict, HeyDeskError, NotFound
from heydesk.models import Call, Conversation
from heydesk.models.call import DONE, LIVE, QUEUED, RINGING, TALKING
from heydesk.ports.llm import LanguageModel
from heydesk.runtime.speech import money
from heydesk.services.agents import AgentService, LiveAgent
from heydesk.services.team import TeamService
from heydesk.shop.config import CallSettings
from heydesk.shop.models import ShopOrder
from heydesk.shop.orders import mobile, spoken_items
from heydesk.shop.store import ShopStore

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Answered:
    call_id: str
    agent: LiveAgent
    conversation_id: str
    said: str  # the call's first line: the agent says it as the call connects


class CallService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        agents: AgentService,
        team: TeamService,
        llm: LanguageModel,
        settings: Settings,
        now: Callable[[], datetime],
    ) -> None:
        self.sessions = sessions
        self.agents = agents
        self.team = team  # tells the owner when a buyer couldn't be reached
        self.llm = llm  # for the first line's message, in the model's own format
        self.settings = settings
        self.now = now
        self.restarted = False  # the first queue check finishes calls a restart left talking
        self.phones: dict[str, asyncio.Queue[dict[str, str]]] = {}  # waiting pages, by mobile

    # The phone page

    @property
    def can_ring(self) -> bool:
        """Whether a call can ring: the /phone page and the voice routes aren't served in
        production (design §12), so there a call would only ever be missed."""
        return self.settings.APP_ENV != "production"

    def clean_number(self, said: str) -> str:
        """The page's mobile, written as an order's is: "+880 1712-345678" → "01712345678"."""
        try:
            return mobile(said)
        except ValueError:  # its message is for the model: "Ask for it again."
            raise HeyDeskError(
                "A Bangladeshi mobile number has 11 digits and starts with 01, like 01712345678."
            ) from None

    def wait_for_calls(self, number: str) -> asyncio.Queue[dict[str, str]]:
        """A page waits for `number`'s calls. The newest page for a mobile gets them."""
        queue: asyncio.Queue[dict[str, str]] = asyncio.Queue()
        self.phones[number] = queue
        return queue

    def stop_waiting(self, number: str, queue: asyncio.Queue[dict[str, str]]) -> None:
        """The page closed; a newer page for the same mobile keeps its place."""
        if self.phones.get(number) is queue:
            del self.phones[number]

    # Into the queue

    async def after_turn(self, agent: LiveAgent, conversation_id: str) -> None:
        """A turn saved something: each new order from it is queued, if the shop has calls on.
        An order is queued by itself once; the owner can call it again.
        Only the shop's own calls: a visitor still can't call the shop (`TAKES_VOICE`)."""
        if not self.can_ring:
            return
        calls = agent.config.calls
        if not calls.enabled:
            return
        for order in await self._store(agent, conversation_id).orders_in_conversation():
            if order.status == "new" and not await self._calls_for(agent.workspace_id, order.id):
                await self._queue(agent, order, calls.wait_minutes)

    async def call_order(self, workspace_id: str, order_id: str) -> Call:
        """The owner's "Call to confirm": first in the queue."""
        agent = await self.agents.by_workspace(workspace_id)
        if not self.can_ring:
            raise Conflict("Calls ring the /phone page, which this server doesn't serve.")
        if not agent.config.calls.enabled:
            raise Conflict("Calls are off. Turn them on in Settings, Calls.")
        order = await self._store(agent).get(ShopOrder, order_id)  # 404 if it isn't theirs
        if order.status != "new":
            raise Conflict(f"Order {order.code} isn't new: only a new order is called.")
        if any(c.status != DONE for c in await self._calls_for(workspace_id, order.id)):
            raise Conflict(f"Order {order.code} already has a call waiting.")
        return await self._queue(agent, order, 0, priority=1)

    async def _queue(
        self, agent: LiveAgent, order: ShopOrder, wait: int, priority: int = 0
    ) -> Call:
        now = self.now()
        call = Call(
            workspace_id=agent.workspace_id,
            agent_id=agent.agent_id,
            order_id=order.id,
            code=order.code,
            name=order.name,
            phone=order.phone,
            priority=priority,
            next_try_at=now + timedelta(minutes=wait),
            created_at=now,
            updated_at=now,
        )
        async with self.sessions() as s, s.begin():
            s.add(call)
        log.info("call queued", extra={"call_id": call.id, "order_id": order.id})
        return call

    # The owner's view of the queue

    async def find(
        self,
        workspace_id: str,
        view: Literal["waiting", "done"] = "waiting",
        order_id: str | None = None,
        limit: int = 100,
    ) -> list[Call]:
        """Waiting: the live calls, then the queue in the order it's called. Done: newest
        first. With `order_id`: that order's calls, newest first."""
        query = select(Call).where(Call.workspace_id == workspace_id)
        if order_id:
            query = query.where(Call.order_id == order_id).order_by(Call.created_at.desc())
        elif view == "done":
            query = query.where(Call.status == DONE).order_by(Call.updated_at.desc())
        else:
            query = query.where(Call.status != DONE).order_by(*QUEUE_ORDER)
        async with self.sessions() as s:
            return list(await s.scalars(query.limit(limit)))

    async def call_now(self, workspace_id: str, call_id: str) -> Call:
        """A queued call goes to the front, due now."""
        async with self.sessions() as s, s.begin():
            call = await self._get(s, call_id, workspace_id)
            if call.status != QUEUED:
                raise Conflict("Only a waiting call can move to the front.")
            call.priority, call.next_try_at, call.updated_at = 1, self.now(), self.now()
        return call

    async def stop(self, workspace_id: str, call_id: str) -> Call:
        """The owner cancels a queued call: the order stays as it is."""
        async with self.sessions() as s, s.begin():
            call = await self._get(s, call_id, workspace_id)
            if call.status != QUEUED:
                raise Conflict("Only a waiting call can be cancelled.")
            call.status, call.outcome, call.updated_at = DONE, "stopped", self.now()
        return call

    # The queue worker

    async def check_queue(self) -> None:
        """One look at the queue (a background job). The first time, a call left talking by a
        restart is done: nothing is talking now."""
        if not self.restarted:
            await self._after_restart()
            self.restarted = True
        await self.ring_due_calls()

    async def _after_restart(self) -> None:
        async with self.sessions() as s, s.begin():
            for call in await s.scalars(select(Call).where(Call.status == TALKING)):
                call.status, call.outcome = DONE, "no_decision"

    async def ring_due_calls(self) -> None:
        """Rings not answered in time are missed; then the next calls due ring, while a line
        is free. Every business's calls share MAX_LIVE_CALLS."""
        now = self.now()
        late = now - timedelta(seconds=self.settings.RING_SECONDS)
        async with self.sessions() as s:
            missed = list(
                await s.scalars(select(Call.id).where(Call.status == RINGING, Call.rang_at < late))
            )
        for call_id in missed:
            await self.mark_missed(call_id)
        async with self.sessions() as s:
            live = await s.scalar(select(func.count()).where(Call.status.in_(LIVE))) or 0
            due = list(
                await s.scalars(
                    select(Call)
                    .where(Call.status == QUEUED, Call.next_try_at <= now)
                    .order_by(*QUEUE_ORDER)
                )
            )
        free = self.settings.MAX_LIVE_CALLS - live
        for call in due:
            if free <= 0:
                break
            try:
                if await self._ring(call):
                    free -= 1
            except NotFound:  # a business suspended, or an order gone: it isn't called
                await self._finish(call.id, "skipped")

    async def _ring(self, call: Call) -> bool:
        """Rings the call's page, if it's time. False when it didn't ring."""
        agent = await self.agents.by_id(call.agent_id)
        calls = agent.config.calls
        if not calls.enabled or not in_hours(calls, agent.config.timezone, self.now()):
            return False  # it waits: until calls are on again, or the morning
        order = await self._store(agent).get(ShopOrder, call.order_id)
        if order.status != "new":  # confirmed or cancelled some other way first
            await self._finish(call.id, "skipped")
            return False
        page = self.phones.get(call.phone)
        async with self.sessions() as s, s.begin():
            row = await self._get(s, call.id)
            row.status, row.tries, row.rang_at, row.updated_at = (
                RINGING,
                row.tries + 1,
                self.now(),
                self.now(),
            )
        if page is None:  # the buyer's phone is off
            log.info("no phone page for the call", extra={"call_id": call.id})
            await self.mark_missed(call.id)
            return False
        page.put_nowait(
            {"call": call.id, "business": agent.config.business_name, "order": call.code}
        )
        log.info("call ringing", extra={"call_id": call.id})
        return True

    async def mark_missed(self, call_id: str) -> None:
        """Not answered, or declined: again later while tries are left; else the owner is told."""
        async with self.sessions() as s:
            call = await self._get(s, call_id)
        if call.status != RINGING:
            return
        agent = await self.agents.by_id(call.agent_id)
        calls = agent.config.calls
        gave_up = call.tries >= calls.tries
        async with self.sessions() as s, s.begin():
            row = await self._get(s, call_id)
            if gave_up:
                row.status, row.outcome = DONE, "no_answer"
            else:
                row.status = QUEUED
                row.next_try_at = self.now() + timedelta(minutes=calls.retry_minutes)
            row.updated_at = self.now()
        log.info("call missed", extra={"call_id": call_id, "gave_up": gave_up})
        if gave_up:
            await self.team.notify(
                call.workspace_id,
                f"📞 No answer from {call.name} ({call.phone}) about order {call.code}, after "
                f"{call.tries} {'try' if call.tries == 1 else 'tries'}. Please call them yourself.",
            )

    # On the call

    async def answer(self, call_id: str) -> Answered:
        """The buyer tapped Answer: a new voice conversation that starts with the call's first
        line, and knows which order it's about (`state.calling`)."""
        async with self.sessions() as s:
            call = await s.get(Call, call_id)
        if call is None or call.status != RINGING:
            raise NotFound("This call has ended.")
        agent = await self.agents.by_id(call.agent_id)
        order = await self._store(agent).get(ShopOrder, call.order_id)
        calls = agent.config.calls
        # str.replace, never str.format: the owner's line may hold other braces
        said = agent.config.fill(calls.opening).replace("{code}", order.code)
        said = said.replace("{total}", money(order.total, order.currency))
        said = said.replace("{items}", spoken_items(order.lines))
        now = self.now()
        conversation = Conversation(
            workspace_id=agent.workspace_id,
            agent_id=agent.agent_id,
            channel="voice",
            state={
                "calling": {"number": order.number, "code": order.code, "said": said},
                "read_back": said,  # the opening line reads the order back (rule 2)
            },
            messages=[self.llm.assistant_message(said)],
            created_at=now,
            updated_at=now,
        )
        async with self.sessions() as s, s.begin():
            s.add(conversation)
            await s.flush()  # its id
            row = await self._get(s, call_id)
            row.status, row.conversation_id, row.updated_at = TALKING, conversation.id, now
        log.info("call answered", extra={"call_id": call_id, "conversation_id": conversation.id})
        return Answered(call_id, agent, conversation.id, said)

    async def mark_ended(self, call_id: str) -> None:
        """The call hung up. How it went is the order's status now."""
        async with self.sessions() as s:
            call = await self._get(s, call_id)
        if call.status != TALKING:
            return
        agent = await self.agents.by_id(call.agent_id)
        order = await self._store(agent).get(ShopOrder, call.order_id)
        outcome = order.status if order.status in ("confirmed", "cancelled") else "no_decision"
        await self._finish(call_id, outcome)
        if outcome == "no_decision":
            await self.team.notify(
                call.workspace_id,
                f"📞 {call.name} ({call.phone}) answered the call about order {call.code} but "
                "didn't confirm or cancel it. It's still new.",
            )

    async def _finish(self, call_id: str, outcome: str) -> None:
        async with self.sessions() as s, s.begin():
            row = await self._get(s, call_id)
            row.status, row.outcome, row.updated_at = DONE, outcome, self.now()
        log.info("call done", extra={"call_id": call_id, "outcome": outcome})

    async def _calls_for(self, workspace_id: str, order_id: str) -> list[Call]:
        async with self.sessions() as s:
            found = await s.scalars(
                select(Call).where(Call.workspace_id == workspace_id, Call.order_id == order_id)
            )
            return list(found)

    async def _get(self, s: AsyncSession, call_id: str, workspace_id: str | None = None) -> Call:
        """One call: the owner's routes pass their `workspace_id`; the worker and the phone go by id."""
        query = select(Call).where(Call.id == call_id)
        if workspace_id is not None:
            query = query.where(Call.workspace_id == workspace_id)
        call = await s.scalar(query)
        if call is None:
            raise NotFound("There's no such call.")
        return call

    def _store(self, agent: LiveAgent, conversation_id: str | None = None) -> ShopStore:
        return self.agents.build_store(agent.workspace_id, conversation_id)


# Who's called next: the live ones first, then "Call now", then the earliest due, the oldest
QUEUE_ORDER = (
    case((Call.status.in_(LIVE), 0), else_=1),
    Call.priority.desc(),
    Call.next_try_at,
    Call.created_at,
)


def in_hours(calls: CallSettings, timezone: str, now: datetime) -> bool:
    """Whether it's inside the shop's calling hours, in its own time."""
    local = now.astimezone(ZoneInfo(timezone)).time()
    return calls.start <= local < calls.end
