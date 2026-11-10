"""A shop's courier: every confirmed order is booked, and each parcel followed.

The owner links Steadfast or Pathao with the shop's own merchant login. A shop with none uses
the fake courier outside production (`fallback`); in production it books by hand.
Every COURIER_SYNC_MINUTES a round books each confirmed order with no parcel (it's `shipped`
then, and the owner is told to pack it) and asks about each shipped one; the courier's final
word makes it delivered or returned. The buyer hears each move in their chat. A courier
problem stops that shop's round, is noted on the link, and the owner hears once."""

import logging
from datetime import UTC, timedelta
from typing import cast

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from heydesk.errors import InvalidConfig, NotFound
from heydesk.models import CourierLink
from heydesk.ports.courier import CourierAccount, CourierApi, CourierError, Parcel
from heydesk.runtime.speech import money
from heydesk.services.agents import AgentService
from heydesk.services.team import TeamService
from heydesk.shop.models import ShopOrder
from heydesk.shop.orders import lines_text
from heydesk.shop.store import STATUSES_TO_BOOK, ShopStore

log = logging.getLogger(__name__)

NAMES = {"steadfast": "Steadfast", "pathao": "Pathao", "fake": "the test courier"}
# ponytail: the words for "the rider has it today", matched in what the courier says. Only
# the fake courier's are known; add Steadfast's or Pathao's once a live parcel shows them
OUT_FOR_DELIVERY = ("out for delivery",)
REVIEW_TOO_LATE = timedelta(days=2)  # a review asked later than this after it's due isn't sent


def out_for_delivery(said: str) -> bool:
    return any(words in said.lower() for words in OUT_FOR_DELIVERY)


def parcel(order: ShopOrder) -> Parcel:
    """The order as the courier takes it: the cash still owed, in whole taka (rule 1)."""
    return Parcel(
        code=order.code,
        name=order.name,
        phone=order.phone,
        address=f"{order.address}, {order.zone}",
        cash_on_delivery=(order.total - order.paid) // 100,
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
        apis: dict[str, CourierApi],  # by kind: "steadfast", "pathao", "fake"
        fallback: str | None,  # the kind a shop with no link uses; None: by hand
        review_after: timedelta = timedelta(hours=24),  # after delivery, "how was it?"
    ) -> None:
        self.sessions = sessions
        self.agents = agents
        self.team = team
        self.apis = apis
        self.fallback = fallback
        self.review_after = review_after

    async def link(self, workspace_id: str, given: CourierAccount) -> CourierLink:
        """Checks the keys with the courier and links it, replacing one already there."""
        agent = await self.agents.get(workspace_id)
        try:
            store_id = await self.apis[given.kind].check(given)
        except CourierError as error:
            raise InvalidConfig({"key": str(error)}) from None
        async with self.sessions() as s, s.begin():
            old = await s.scalar(
                select(CourierLink).where(CourierLink.workspace_id == workspace_id)
            )
            if old is not None:
                await s.delete(old)
                await s.flush()
            link = CourierLink(
                workspace_id=workspace_id,
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
        log.info("courier linked", extra={"workspace_id": workspace_id, "kind": given.kind})
        return link

    async def get(self, workspace_id: str) -> CourierLink:
        async with self.sessions() as s:
            link = await s.scalar(
                select(CourierLink).where(CourierLink.workspace_id == workspace_id)
            )
        if link is None:
            raise NotFound("No courier is linked.")
        return link

    async def unlink(self, workspace_id: str) -> None:
        """Parcels already booked stay with the courier; they aren't followed any more."""
        async with self.sessions() as s, s.begin():
            link = await s.scalar(
                select(CourierLink).where(CourierLink.workspace_id == workspace_id)
            )
            if link is not None:
                await s.delete(link)

    async def sync(self, workspace_id: str) -> None:
        """Asks the buyers of yesterday's deliveries how it was; then books the confirmed
        and packed orders with no parcel, and asks about each shipped one, with the shop's
        courier, or the fallback when none is linked."""
        shop = await self.agents.store(workspace_id)
        await self._ask_reviews(shop)
        try:
            link: CourierLink | None = await self.get(workspace_id)
        except NotFound:
            link = None
        if link is None and self.fallback is None:
            return  # booked by hand
        acct = account(link) if link else CourierAccount(cast(str, self.fallback))
        api, name = self.apis[acct.kind], NAMES[acct.kind]
        problem = ""
        try:
            for order in await shop.orders_for_courier("confirmed"):
                try:
                    parcel_id = await api.book(acct, parcel(order))
                except CourierError as error:
                    raise CourierError(f"{order.code} wasn't booked with {name}: {error}") from None
                await self._tell_visitor_status(await shop.parcel_booked(order.id, parcel_id))
                await self.team.notify(
                    workspace_id, f"📦 {order.code} is booked with {name} ({parcel_id}). Pack it."
                )
            for order in await shop.orders_for_courier("shipped"):
                tracked = await api.status(acct, order.parcel_id)
                if tracked.status_text == order.courier_status and tracked.final_status is None:
                    continue
                moved = await shop.update_courier_status(
                    order.id, tracked.status_text, tracked.final_status
                )
                if moved.status != order.status:
                    await self._tell_visitor_status(moved)
                elif not order.reminded and out_for_delivery(tracked.status_text):
                    await self._remind(shop, moved)
                if tracked.final_status == "returned":
                    await self.team.notify(
                        workspace_id, f"↩️ {order.code} is coming back. Put its items back in stock."
                    )
        except CourierError as error:  # the rest wait for the next round
            problem = str(error)
            log.warning("courier failed", extra={"workspace_id": workspace_id, "kind": acct.kind})
        if link is None:
            return
        async with self.sessions() as s, s.begin():
            saved = await s.get_one(CourierLink, link.id)
            told, saved.problem = saved.problem, problem
        if problem and problem != told:
            await self.team.notify(workspace_id, f"⚠️ Your courier: {problem}")

    async def _tell_visitor_status(self, order: ShopOrder) -> None:
        if order.conversation_id and (line := order.status_update_text()):
            await self.team.tell_visitor(order.conversation_id, line)

    async def _remind(self, shop: ShopStore, order: ShopOrder) -> None:
        """Out for delivery: the buyer keeps the cash ready, once, inside the window."""
        await shop.mark_notified(order.id, "reminded")
        if order.conversation_id is None:
            return
        due = order.total - order.paid
        pay = (
            f"Please keep {money(due, order.currency)} ready for the rider."
            if due
            else "It's paid: there's nothing to pay the rider."
        )
        line = f"🛵 Your order {order.code} is out for delivery today. {pay}"
        await self.team.tell_visitor_if_allowed(order.conversation_id, line)

    async def _ask_reviews(self, shop: ShopStore) -> None:
        """A day after delivery: one question with two buttons, inside the window. An
        order delivered long before (when this began, or the shop's round was down) isn't
        asked, only marked."""
        now = shop.now()
        for order in await shop.orders_to_review(now - self.review_after):
            await shop.mark_notified(order.id, "review_asked")
            delivered = (
                order.updated_at
                if order.updated_at.tzinfo
                else order.updated_at.replace(tzinfo=UTC)
            )
            if (
                order.conversation_id is None
                or now - delivered > self.review_after + REVIEW_TOO_LATE
            ):
                continue
            line = f"How was your order {order.code}? Tap 👍 or 👎, or write to us."
            await self.team.tell_visitor_if_allowed(order.conversation_id, line, ["👍", "👎"])

    async def sync_all(self) -> None:
        """A round for each shop with a parcel to book or follow (a background job). One
        failing doesn't stop the others."""
        async with self.sessions() as s:
            busy = await s.scalars(
                select(ShopOrder.workspace_id)
                .where(
                    or_(
                        and_(ShopOrder.status.in_(STATUSES_TO_BOOK), ShopOrder.parcel_id == ""),
                        and_(ShopOrder.status == "shipped", ShopOrder.parcel_id != ""),
                        and_(  # its buyer is asked how it was
                            ShopOrder.status == "delivered",
                            ShopOrder.review_asked.is_(False),
                            ShopOrder.conversation_id.is_not(None),
                        ),
                    )
                )
                .distinct()
            )
            shops = list(busy)
        for workspace_id in shops:
            try:
                await self.sync(workspace_id)
            except Exception:
                log.exception("courier round failed", extra={"workspace_id": workspace_id})
