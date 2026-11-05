"""Paying online before something is placed: a shop's delivery charge. Each is a `Payable` table, and each business is paid into its own
SSLCommerz store, its own bKash account, or both, which its owner connects here.

A desk's writing tool saves the row `unpaid` and sends the visitor `/pay/<id>`. That page
starts a payment with the gateway the visitor picks. When the gateway sends word back (the
visitor's browser, and SSLCommerz itself, both to URLs naming the row), `check_payment` asks the
business's account with that gateway whether it really went through, for this row and this
amount, and only then moves it on: to `new`, or a shop order to `confirmed` (`PAID_STATUS`).
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
from heydesk.models import Conversation, PaymentAccount
from heydesk.models.customer_request import UNPAID
from heydesk.ports.payments import (
    PROVIDERS,
    Account,
    Checkout,
    Gateways,
    PaymentUnavailable,
    Provider,
)
from heydesk.runtime.speech import money
from heydesk.services.team import TeamService
from heydesk.services.woo import WooService
from heydesk.shop.models import ShopOrder

log = logging.getLogger(__name__)

NOTIFY_PATH = "/api/payments/sslcommerz/{record_id}"  # SSLCommerz's own post, server to server
NAMES = {"sslcommerz": "SSLCommerz", "bkash": "bKash"}
type Payable = ShopOrder
PAYABLE_TABLES: tuple[type[Payable], ...] = (ShopOrder,)


class PaymentService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        gateways: Gateways,
        team: TeamService,
        woo: WooService,
        settings: Settings,
        now: Callable[[], datetime],
    ) -> None:
        self.sessions = sessions
        self.gateways = gateways
        self.team = team
        self.woo = woo  # a paid shop order goes into a linked WooCommerce store at once
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
        self, workspace_id: str, provider: Provider, account: Account
    ) -> PaymentAccount:
        """Checks the account with its gateway, then keeps it, in place of any before."""
        try:
            await self.gateways[provider].verify(account)
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

    async def _gateway_account(self, workspace_id: str, provider: Provider) -> Account:
        saved = await self.account(workspace_id, provider)
        if saved is None:
            raise PaymentUnavailable(f"this business takes no payments by {NAMES[provider]}")
        return Account(
            saved.store_id,
            saved.password,
            saved.sandbox,
            app_key=saved.app_key,
            app_secret=saved.app_secret or "",
            username=saved.username,
        )

    # Paying

    async def find(self, record_id: str) -> Payable | None:
        """The order with this id."""
        async with self.sessions() as s:
            for table in PAYABLE_TABLES:
                found: Payable | None = await s.get(table, record_id)
                if found is not None:
                    return found
        return None

    async def start(self, record: Payable, provider: Provider) -> str:
        """The gateway's page for paying an unpaid order. SSLCommerz posts the
        visitor back to `/pay/<id>/back`; bKash sends them to `/pay/<id>/bkash`."""
        base = self.settings.public_url
        back = "back" if provider == "sslcommerz" else "bkash"
        return await self.gateways[provider].start(
            await self._gateway_account(record.workspace_id, provider),
            Checkout(
                order_id=record.id,
                amount=record.amount_due,
                currency=record.currency,
                what=f"{record.PAY_FOR[4:].capitalize()} for {record.code}",  # minus "the "
                name=record.name,
                phone=record.phone,
                address=getattr(record, "address", ""),
                back_url=f"{base}/pay/{record.id}/{back}",
                notify_url=base + NOTIFY_PATH.replace("{record_id}", record.id),
            ),
        )

    async def check_payment(
        self, record_id: str, payment_id: str, provider: Provider = "sslcommerz"
    ) -> Payable | None:
        """Checks a payment a gateway sent word of for a row and, if it paid that unpaid row
        in full, places it. The visitor's return and SSLCommerz's notice both come here; only
        the first one changes anything. Returns the row."""
        record = await self.find(record_id)
        if record is None:
            return None
        paid = await self.gateways[provider].check(
            await self._gateway_account(record.workspace_id, provider), payment_id
        )
        if paid is None or paid.order_id != record.id:
            return record
        if (paid.amount, paid.currency) != (record.amount_due, record.currency):
            log.warning("payment amount differs", extra={"record_id": record.id})
            return record

        table = type(record)
        async with self.sessions() as s, s.begin():
            moved = await s.execute(  # only from unpaid, so two posts can't both place it
                update(table)
                .where(table.id == record.id, table.status == UNPAID)
                .values(
                    status=record.PAID_STATUS,
                    paid=paid.amount,
                    payment_ref=paid.reference,
                    updated_at=self.now(),
                )
            )
        record = await self.find(record.id)
        assert record is not None
        amount = money(paid.amount, paid.currency)
        if moved.rowcount == 1:  # type: ignore[attr-defined]
            log.info("paid online", extra={"record_id": record.id})
            await self._tell_visitor_paid(record, amount)
        elif record.status == "cancelled" and not record.paid:
            await self._alert(
                record,
                f"⚠️ {record.code} was paid after it was cancelled: refund {amount} "
                f"({paid.reference}).",
            )
        return record

    async def _tell_visitor_paid(self, record: Payable, amount: str) -> None:
        if record.conversation_id:
            await self.team.tell_visitor(
                record.conversation_id,
                f"✅ Payment received: {amount} for {record.PAY_FOR}. {record.next_step_text()}",
            )
        await self._alert(
            record,
            f"💳 New {record.code}: {record.PAY_FOR} paid online, {amount}\n"
            f"{record.alert_summary()}; name {record.name}; phone {record.phone}",
        )
        self.woo.push_soon(record.workspace_id)

    async def _alert(self, record: Payable, text: str) -> None:
        if record.conversation_id is None:
            return
        async with self.sessions() as s:
            conversation = await s.scalar(
                select(Conversation).where(
                    Conversation.id == record.conversation_id,
                    Conversation.workspace_id == record.workspace_id,
                )
            )
        if conversation is not None:
            await self.team.alert(conversation, text)
