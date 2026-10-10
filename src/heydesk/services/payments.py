"""Paying a shop's delivery charge online before the order is placed (D47).

The agent saves the order `unpaid` and sends the buyer `/pay/<order id>`. That page starts a
payment with the gateway. When the gateway posts back (the buyer's browser, and the gateway
itself), `settle` asks the gateway whether it really went through, for this order and this
amount, and only then makes the order `new`. Nothing the agent says can do that.

These look orders up by their id alone, across businesses, like Telegram's webhook looks up
its channel: the id is long and random, and it's the gateway that names it.
"""

import logging
from collections.abc import Callable
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from heydesk.config import Settings
from heydesk.desks.shop.models import ShopOrder
from heydesk.desks.shop.values import lines_text
from heydesk.models import Conversation
from heydesk.ports.payments import Checkout, PaymentGateway
from heydesk.runtime.speech import money
from heydesk.services.team import TeamService

log = logging.getLogger(__name__)

NOTIFY_PATH = "/api/payments/sslcommerz"  # the gateway's own post, server to server


class PaymentService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        gateway: PaymentGateway,
        team: TeamService,
        settings: Settings,
        now: Callable[[], datetime],
    ) -> None:
        self.sessions = sessions
        self.gateway = gateway
        self.team = team
        self.settings = settings
        self.now = now

    async def order(self, order_id: str) -> ShopOrder | None:
        async with self.sessions() as s:
            return await s.get(ShopOrder, order_id)

    async def start(self, order: ShopOrder) -> str:
        """The gateway's page for paying an unpaid order's delivery charge."""
        base = self.settings.public_url
        return await self.gateway.start(
            Checkout(
                order_id=order.id,
                amount=order.delivery_charge,
                currency=order.currency,
                what=f"Delivery charge for order {order.code}",
                name=order.name,
                phone=order.phone,
                address=order.address,
                back_url=f"{base}/pay/{order.id}/back",
                notify_url=base + NOTIFY_PATH,
            )
        )

    async def settle(self, payment_id: str) -> ShopOrder | None:
        """Checks a payment the gateway posted back and, if it paid an unpaid order's delivery
        charge in full, places the order. The buyer's return and the gateway's notice both
        come here; only the first one changes anything. Returns the order it was for."""
        paid = await self.gateway.check(payment_id)
        if paid is None:
            return None
        order = await self.order(paid.order_id)
        if order is None:
            log.warning("payment for no order", extra={"order_id": paid.order_id})
            return None
        if (paid.amount, paid.currency) != (order.delivery_charge, order.currency):
            log.warning("payment amount differs", extra={"order_id": order.id})
            return order

        async with self.sessions() as s, s.begin():
            moved = await s.execute(  # only from unpaid, so two posts can't both place it
                update(ShopOrder)
                .where(ShopOrder.id == order.id, ShopOrder.status == "unpaid")
                .values(
                    status="new",
                    paid=paid.amount,
                    payment_ref=paid.reference,
                    updated_at=self.now(),
                )
            )
        order = await self.order(order.id)
        assert order is not None
        if moved.rowcount == 1:  # type: ignore[attr-defined]
            log.info("delivery charge paid", extra={"order_id": order.id})
            await self._placed(order)
        elif order.status == "cancelled" and not order.paid:
            await self._alert(
                order,
                f"⚠️ Order {order.code} was paid after it was cancelled: "
                f"refund {money(paid.amount, paid.currency)} ({paid.reference}).",
            )
        return order

    async def _placed(self, order: ShopOrder) -> None:
        cod = money(order.total - order.paid, order.currency)
        if order.conversation_id:
            await self.team.tell_visitor(
                order.conversation_id,
                f"✅ Payment received. Your order {order.code} is placed; the rest, {cod}, is "
                "paid in cash on delivery. The shop will call you to confirm before sending it.",
            )
        await self._alert(
            order,
            f"🛍️ New order {order.code}, delivery charge paid online\n"
            f"{lines_text(order.lines)}; delivery {order.zone}; total "
            f"{money(order.total, order.currency)}, {cod} to collect on delivery; to "
            f"{order.address}; name {order.name}; mobile {order.phone}"
            + (f"; note: {order.note}" if order.note else ""),
        )

    async def _alert(self, order: ShopOrder, text: str) -> None:
        if order.conversation_id is None:
            return
        async with self.sessions() as s:
            conv = await s.scalar(
                select(Conversation).where(
                    Conversation.id == order.conversation_id,
                    Conversation.workspace_id == order.workspace_id,
                )
            )
        if conv is not None:
            await self.team.alert(conv, text)
