"""A shop's courier (D90): the agent books each confirmed order's parcel and follows it.

The owner links Steadfast or Pathao once. An order that becomes `confirmed` (a yes on the
shop's call, or the owner) is booked at once, in the background: it's `shipped` then, and the
owner is told to pack it. Every COURIER_SYNC_MINUTES a round books what's left and asks the
courier about each shipped parcel; its final word makes the order delivered or returned. A
problem is noted, and the owner hears once.
"""

import asyncio
import logging
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from heydesk.desks.shop.models import ShopOrder
from heydesk.desks.shop.store import ShopStore
from heydesk.desks.shop.values import lines_text
from heydesk.errors import InvalidConfig, NotFound
from heydesk.models import CourierLink
from heydesk.ports.courier import CourierAccount, CourierApi, CourierError, Parcel
from heydesk.services.agents import AgentService
from heydesk.services.team import TeamService

log = logging.getLogger(__name__)

NAMES = {"steadfast": "Steadfast", "pathao": "Pathao"}


def parcel(order: ShopOrder) -> Parcel:
    """The order as the courier takes it: the cash still owed, in whole taka (rule 1)."""
    return Parcel(
        code=order.code,
        name=order.name,
        phone=order.phone,
        address=f"{order.address}, {order.zone}",
        cod=(order.total - order.paid) // 100,
        items=lines_text(order.lines),
        quantity=sum(line["quantity"] for line in order.lines),
        note=order.note,
    )


def account(link: CourierLink) -> CourierAccount:
    return CourierAccount(
        link.kind,
        link.key,
        link.secret,
        link.username,
        link.password,
        link.store_id,
        link.sandbox,
    )


class CourierService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        agents: AgentService,
        team: TeamService,
        apis: dict[str, CourierApi],  # by kind: "steadfast", "pathao"
    ) -> None:
        self.sessions = sessions
        self.agents = agents
        self.team = team
        self.apis = apis
        self.locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)  # one round each
        self.tasks: set[asyncio.Task[None]] = set()  # rounds running in the background

    async def link(self, ws: str, given: CourierAccount) -> CourierLink:
        """Checks the keys with the courier and links it, replacing one already there."""
        agent = await self.agents.get(ws)
        if agent.kind != "shop":
            raise NotFound("Only a shop can link a courier.")
        if given.kind not in self.apis:
            raise InvalidConfig({"kind": "Pick steadfast or pathao."})
        try:
            store_id = await self.apis[given.kind].check(given)
        except CourierError as error:
            raise InvalidConfig({"key": str(error)}) from None
        async with self.sessions() as s, s.begin():
            old = await s.scalar(select(CourierLink).where(CourierLink.workspace_id == ws))
            if old is not None:
                await s.delete(old)
                await s.flush()
            link = CourierLink(
                workspace_id=ws,
                agent_id=agent.id,
                kind=given.kind,
                key=given.key.strip(),
                secret=given.secret.strip(),
                username=given.username.strip(),
                password=given.password,
                store_id=store_id,
                sandbox=given.sandbox,
            )
            s.add(link)
        log.info("courier linked", extra={"workspace_id": ws, "kind": given.kind})
        return link

    async def get(self, ws: str) -> CourierLink:
        async with self.sessions() as s:
            link = await s.scalar(select(CourierLink).where(CourierLink.workspace_id == ws))
        if link is None:
            raise NotFound("No courier is linked.")
        return link

    async def unlink(self, ws: str) -> None:
        """Parcels already booked stay with the courier; they aren't followed any more."""
        async with self.sessions() as s, s.begin():
            link = await s.scalar(select(CourierLink).where(CourierLink.workspace_id == ws))
            if link is not None:
                await s.delete(link)

    def book_soon(self, ws: str) -> None:
        """An order may have been confirmed: a round now, in the background, so the chat or
        the owner's click never waits on the courier."""
        task = asyncio.create_task(self._quietly(ws))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def _quietly(self, ws: str) -> None:
        try:
            async with self.sessions() as s:
                linked = await s.scalar(
                    select(CourierLink.id).where(CourierLink.workspace_id == ws)
                )
            if linked:
                await self.sync(ws)
        except Exception:
            log.exception("courier round failed", extra={"workspace_id": ws})

    async def sync(self, ws: str) -> CourierLink:
        """Books the confirmed orders with no parcel, then asks about each shipped one. Never
        raises for the courier's own problems: they're `problem`."""
        link = await self.get(ws)
        shop = await self._store(ws)
        api, acct, name = self.apis[link.kind], account(link), NAMES[link.kind]
        async with self.locks[ws]:
            problem = ""
            for order in await shop.orders_to_look_up():  # before it's confirmed (D91)
                try:
                    record = await api.record(acct, order.phone)
                except CourierError:  # only a hint: tried again next round
                    log.warning("courier record failed", extra={"workspace_id": ws})
                    break
                if record is None:  # this courier can't tell
                    break
                await shop.record_found(order.id, record)
                await self.team.notify(ws, f"🔎 {order.code}, the buyer's mobile on {record}")
            for order in await shop.orders_for_courier("confirmed"):
                try:
                    booked = await api.book(acct, parcel(order))
                except CourierError as error:  # the rest wait: tried again next round
                    problem = f"{order.code} wasn't booked with {name}: {error}"
                    break
                await shop.parcel_booked(order.id, booked.parcel_id, f"{name} {booked.parcel_id}")
                await self.team.notify(
                    ws,
                    f"📦 {order.code} is booked with {name} ({booked.parcel_id}). "
                    "Pack it; the rider will pick it up.",
                )
            for order in await shop.orders_for_courier("shipped"):
                try:
                    tracked = await api.status(acct, order.parcel_id)
                except CourierError as error:
                    problem = problem or f"{name}: {error}"
                    break
                if tracked.said == order.courier_status and tracked.ending is None:
                    continue
                await shop.courier_said(order.id, tracked.said, tracked.ending)
                if tracked.ending == "returned":
                    await self.team.notify(
                        ws,
                        f"↩️ {order.code} is coming back: {name} says {tracked.said}. Put its "
                        "items back in stock when it arrives.",
                    )
        async with self.sessions() as s, s.begin():
            saved = await s.get_one(CourierLink, link.id)
            told, saved.problem = saved.problem, problem
        if problem and problem != told:
            await self.team.notify(ws, f"⚠️ Your courier: {problem}")
        log.info("courier synced", extra={"workspace_id": ws, "problem": bool(problem)})
        return saved

    async def run(self, every: float) -> None:
        """Every `every` seconds, until cancelled (the app's lifespan starts it), a round for
        each linked courier. One failing doesn't stop the others."""
        while True:
            await asyncio.sleep(every)
            async with self.sessions() as s:
                linked = list(await s.scalars(select(CourierLink.workspace_id)))
            for ws in linked:
                try:
                    await self.sync(ws)
                except Exception:
                    log.exception("courier round failed", extra={"workspace_id": ws})

    async def _store(self, ws: str) -> ShopStore:
        store = await self.agents.store(ws)
        assert isinstance(store, ShopStore)
        return store
