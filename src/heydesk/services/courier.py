"""The shop's courier (D112): every confirmed order is booked, and each parcel followed.

Every COURIER_SYNC_MINUTES a round books each confirmed order with no parcel (it's `shipped`
then, and the owner is told to pack it) and asks about each shipped one; the courier's final
word makes it delivered or returned. The buyer hears each move in their chat. A courier
problem stops that shop's round and is tried again next time."""

import asyncio
import logging
from typing import cast

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from heydesk.desks.shop.models import ShopOrder
from heydesk.desks.shop.store import ShopStore
from heydesk.desks.shop.values import lines_text
from heydesk.ports.courier import CourierApi, CourierError, Parcel
from heydesk.services.agents import AgentService
from heydesk.services.team import TeamService

log = logging.getLogger(__name__)


def parcel(order: ShopOrder) -> Parcel:
    """The order as the courier takes it: the cash still owed, in whole taka (rule 1)."""
    return Parcel(
        code=order.code,
        name=order.name,
        phone=order.phone,
        address=f"{order.address}, {order.zone}",
        cod=(order.total - order.paid) // 100,
        items=lines_text(order.lines),
        note=order.note,
    )


class CourierService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        agents: AgentService,
        team: TeamService,
        api: CourierApi,
    ) -> None:
        self.sessions = sessions
        self.agents = agents
        self.team = team
        self.api = api

    async def sync(self, ws: str) -> None:
        """Books the confirmed orders with no parcel, then asks about each shipped one."""
        shop = cast(ShopStore, await self.agents.store(ws, "shop"))
        try:
            for order in await shop.orders_for_courier("confirmed"):
                parcel_id = await self.api.book(parcel(order))
                await self._moved(ws, await shop.parcel_booked(order.id, parcel_id))
                await self.team.notify(
                    ws, f"📦 {order.code} is booked with the courier ({parcel_id}). Pack it."
                )
            for order in await shop.orders_for_courier("shipped"):
                tracked = await self.api.status(order.parcel_id)
                if tracked.said == order.courier_status and tracked.ending is None:
                    continue
                moved = await shop.courier_said(order.id, tracked.said, tracked.ending)
                if moved.status != order.status:
                    await self._moved(ws, moved)
                if tracked.ending == "returned":
                    await self.team.notify(
                        ws, f"↩️ {order.code} is coming back. Put its items back in stock."
                    )
        except CourierError as error:  # the rest wait for the next round
            log.warning("courier failed: %s", error, extra={"workspace_id": ws})

    async def _moved(self, ws: str, order: ShopOrder) -> None:
        if order.conversation_id and (line := order.news()):
            await self.team.tell_visitor(order.conversation_id, line)

    async def run(self, every: float) -> None:
        """Every `every` seconds, until cancelled (the app's lifespan starts it), a round for
        each shop with a parcel to book or follow. One failing doesn't stop the others."""
        while True:
            await asyncio.sleep(every)
            async with self.sessions() as s:
                busy = await s.scalars(
                    select(ShopOrder.workspace_id)
                    .where(
                        or_(
                            and_(ShopOrder.status == "confirmed", ShopOrder.parcel_id == ""),
                            and_(ShopOrder.status == "shipped", ShopOrder.parcel_id != ""),
                        )
                    )
                    .distinct()
                )
                shops = list(busy)
            for ws in shops:
                try:
                    await self.sync(ws)
                except Exception:
                    log.exception("courier round failed", extra={"workspace_id": ws})
