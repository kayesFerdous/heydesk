"""Paying a shop order's delivery charge online before it's placed. Each business is paid into
its own SSLCommerz store, its own bKash account, or both, which its owner connects here.

`place_order` saves the order `unpaid` and sends the visitor `/pay/<id>`. That page
starts a payment with the gateway the visitor picks. When the gateway sends word back (the
visitor's browser, and SSLCommerz itself, both to URLs naming the row), `confirm_payment` asks the
business's account with that gateway whether it really went through, for this row and this
amount, and only then moves it on, to `confirmed`.
Nothing the agent says can do that.

These look rows up by their id alone, across businesses, like Telegram's webhook looks up
its channel: the id is long and random, and it's the gateway that names it.
"""

import logging
from collections.abc import Callable
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from heydesk.config import Settings
from heydesk.errors import HeyDeskError, NotFound
from heydesk.models import PaymentAccount
from heydesk.models.record import UNPAID
from heydesk.ports.payments import (
    PROVIDERS,
    Checkout,
    GatewayAccount,
    Gateways,
    PaymentUnavailable,
    Provider,
)
from heydesk.runtime.speech import money
from heydesk.services.orders import OrderService
from heydesk.services.team import TeamService
from heydesk.shop.models import ShopOrder

log = logging.getLogger(__name__)

NOTIFY_PATH = "/api/payments/sslcommerz/{order_id}"  # SSLCommerz's own post, server to server
NAMES = {"sslcommerz": "SSLCommerz", "bkash": "bKash"}


class PaymentService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        gateways: Gateways,
        team: TeamService,
        orders: OrderService,
        settings: Settings,
        now: Callable[[], datetime],
    ) -> None:
        self.sessions = sessions
        self.gateways = gateways
        self.team = team
        self.orders = orders  # a paid order: the owner hears, and it goes into a linked store
        self.settings = settings
        self.now = now

    # The business's own accounts

    async def account(self, workspace_id: str, provider: Provider) -> PaymentAccount | None:
        async with self.sessions() as s:
            return await s.scalar(
                select(PaymentAccount).where(
                    PaymentAccount.workspace_id == workspace_id, PaymentAccount.provider == provider
                )
            )

    async def providers(self, workspace_id: str) -> list[Provider]:
        """The gateways this business is connected to, in the order the pay page shows them."""
        async with self.sessions() as s:
            found = set(
                await s.scalars(
                    select(PaymentAccount.provider).where(
                        PaymentAccount.workspace_id == workspace_id
                    )
                )
            )
        return [p for p in PROVIDERS if p in found]

    async def connect(
        self, workspace_id: str, provider: Provider, account: GatewayAccount
    ) -> PaymentAccount:
        """Checks the account with its gateway, then keeps it, in place of any before."""
        try:
            await self.gateways[provider].verify_account(account)
        except PaymentUnavailable as error:
            raise HeyDeskError(f"{NAMES[provider]} didn't accept these: {error}") from None
        async with self.sessions() as s, s.begin():
            saved = await s.scalar(
                select(PaymentAccount).where(
                    PaymentAccount.workspace_id == workspace_id, PaymentAccount.provider == provider
                )
            )
            if saved is None:
                saved = PaymentAccount(
                    workspace_id=workspace_id, provider=provider, created_at=self.now()
                )
                s.add(saved)
            saved.store_id, saved.password = account.store_id, account.password
            saved.app_key, saved.username = account.app_key, account.username
            saved.app_secret = account.app_secret or None
            saved.sandbox = account.sandbox
        log.info(
            "payment account connected",
            extra={"workspace_id": workspace_id, "provider": provider, "sandbox": account.sandbox},
        )
        return saved

    async def disconnect(self, workspace_id: str, provider: Provider) -> None:
        """Payments through it stop. With none left, the agent no longer asks for any, and
        unpaid ones can't be paid."""
        async with self.sessions() as s, s.begin():
            saved = await s.scalar(
                select(PaymentAccount).where(
                    PaymentAccount.workspace_id == workspace_id, PaymentAccount.provider == provider
                )
            )
            if saved is None:
                raise NotFound(f"No {NAMES[provider]} account is connected.")
            await s.delete(saved)

    async def _gateway_account(self, workspace_id: str, provider: Provider) -> GatewayAccount:
        saved = await self.account(workspace_id, provider)
        if saved is None:
            raise PaymentUnavailable(f"this business takes no payments by {NAMES[provider]}")
        return GatewayAccount(
            saved.store_id,
            saved.password,
            saved.sandbox,
            app_key=saved.app_key,
            app_secret=saved.app_secret or "",
            username=saved.username,
        )

    # Paying

    async def find(self, order_id: str) -> ShopOrder | None:
        """The order with this id."""
        async with self.sessions() as s:
            return await s.get(ShopOrder, order_id)

    async def start(self, order: ShopOrder, provider: Provider) -> str:
        """The gateway's page for paying an unpaid order. SSLCommerz posts the
        visitor back to `/pay/<id>/back`; bKash sends them to `/pay/<id>/bkash`."""
        base = self.settings.public_url
        back = "back" if provider == "sslcommerz" else "bkash"
        return await self.gateways[provider].start(
            await self._gateway_account(order.workspace_id, provider),
            Checkout(
                order_id=order.id,
                amount=order.amount_due,
                currency=order.currency,
                what=f"{order.PAY_FOR[4:].capitalize()} for {order.code}",  # minus "the "
                name=order.name,
                phone=order.phone,
                address=order.address,
                back_url=f"{base}/pay/{order.id}/{back}",
                notify_url=base + NOTIFY_PATH.replace("{order_id}", order.id),
            ),
        )

    async def confirm_payment(
        self, order_id: str, payment_id: str, provider: Provider = "sslcommerz"
    ) -> ShopOrder | None:
        """Checks a payment a gateway sent word of for a row and, if it paid that unpaid row
        in full, places it. The visitor's return and SSLCommerz's notice both come here; only
        the first one changes anything. Returns the row."""
        order = await self.find(order_id)
        if order is None:
            return None
        paid = await self.gateways[provider].fetch_payment(
            await self._gateway_account(order.workspace_id, provider), payment_id
        )
        if paid is None or paid.order_id != order.id:
            return order
        if (paid.amount, paid.currency) != (order.amount_due, order.currency):
            log.warning("payment amount differs", extra={"order_id": order.id})
            return order

        async with self.sessions() as s, s.begin():
            moved = await s.execute(  # only from unpaid, so two posts can't both place it
                update(ShopOrder)
                .where(ShopOrder.id == order.id, ShopOrder.status == UNPAID)
                .values(
                    status="confirmed",
                    paid=paid.amount,
                    payment_ref=paid.reference,
                    updated_at=self.now(),
                )
            )
        order = await self.find(order.id)
        assert order is not None
        amount = money(paid.amount, paid.currency)
        if moved.rowcount == 1:  # type: ignore[attr-defined]
            log.info("paid online", extra={"order_id": order.id})
            await self._tell_visitor_paid(order, amount)
        elif order.status == "cancelled" and not order.paid:
            await self.orders.alert(
                order.workspace_id,
                await self.orders.conversation_of(order),
                f"⚠️ {order.code} was paid after it was cancelled: refund {amount} "
                f"({paid.reference}).",
            )
        return order

    async def _tell_visitor_paid(self, order: ShopOrder, amount: str) -> None:
        if order.conversation_id:
            await self.team.tell_visitor(
                order.workspace_id,
                order.conversation_id,
                f"✅ Payment received: {amount} for {order.PAY_FOR}. {order.next_step_text()}",
            )
        await self.orders.order_saved(
            order.workspace_id,
            await self.orders.conversation_of(order),
            f"💳 New {order.code}: {order.PAY_FOR} paid online, {amount}\n"
            f"{order.alert_summary()}; name {order.name}; phone {order.phone}",
        )
