"""Paying online before something is placed (D47, D48): a shop's or restaurant's delivery
charge, a clinic's deposit. Each is a `Payable` table, and each business is paid into its own
SSLCommerz store, which its owner connects here (D49).

A desk's writing tool saves the row `unpaid` and sends the visitor `/pay/<id>`. That page
starts a payment with the gateway. When the gateway posts back (the visitor's browser, and
the gateway itself, both to URLs naming the row), `settle` asks the business's gateway
account whether it really went through, for this row and this amount, and only then makes it
`new`. Nothing the agent says can do that.

These look rows up by their id alone, across businesses, like Telegram's webhook looks up
its channel: the id is long and random, and it's the gateway that names it.
"""

import logging
from collections.abc import Callable
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from heydesk.config import Settings
from heydesk.desks.clinic.models import Appointment
from heydesk.desks.restaurant.models import Order
from heydesk.desks.shop.models import ShopOrder
from heydesk.errors import HeyDeskError, NotFound
from heydesk.models import Conversation, PaymentAccount
from heydesk.models.taken import FIRST_STATUS, UNPAID
from heydesk.ports.payments import Account, Checkout, PaymentGateway, PaymentUnavailable
from heydesk.runtime.speech import money
from heydesk.services.team import TeamService

log = logging.getLogger(__name__)

NOTIFY_PATH = "/api/payments/sslcommerz/{record_id}"  # the gateway's own post, server to server
type Payable = ShopOrder | Order | Appointment
PAYABLE: tuple[type[Payable], ...] = (ShopOrder, Order, Appointment)


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

    # The business's own store (D49)

    async def account(self, ws: str) -> PaymentAccount | None:
        async with self.sessions() as s:
            return await s.scalar(select(PaymentAccount).where(PaymentAccount.workspace_id == ws))

    async def connect(self, ws: str, store_id: str, password: str, sandbox: bool) -> PaymentAccount:
        """Checks the store with SSLCommerz, then keeps it, in place of any before."""
        account = Account(store_id, password, sandbox)
        try:
            await self.gateway.verify(account)
        except PaymentUnavailable as error:
            raise HeyDeskError(f"SSLCommerz didn't accept these: {error}") from None
        async with self.sessions() as s, s.begin():
            saved = await s.scalar(select(PaymentAccount).where(PaymentAccount.workspace_id == ws))
            if saved is None:
                saved = PaymentAccount(workspace_id=ws, created_at=self.now())
                s.add(saved)
            saved.store_id, saved.password, saved.sandbox = store_id, password, sandbox
        log.info("payment account connected", extra={"workspace_id": ws, "sandbox": sandbox})
        return saved

    async def disconnect(self, ws: str) -> None:
        """Payments stop: the agent no longer asks for any, and unpaid ones can't be paid."""
        async with self.sessions() as s, s.begin():
            saved = await s.scalar(select(PaymentAccount).where(PaymentAccount.workspace_id == ws))
            if saved is None:
                raise NotFound("No SSLCommerz account is connected.")
            await s.delete(saved)

    async def _gateway_account(self, ws: str) -> Account:
        saved = await self.account(ws)
        if saved is None:
            raise PaymentUnavailable("this business takes no payments online")
        return Account(saved.store_id, saved.password, saved.sandbox)

    # Paying

    async def find(self, record_id: str) -> Payable | None:
        """The order or appointment with this id, whichever table it's in."""
        async with self.sessions() as s:
            for table in PAYABLE:
                found: Payable | None = await s.get(table, record_id)
                if found is not None:
                    return found
        return None

    async def start(self, record: Payable) -> str:
        """The gateway's page for paying an unpaid order or appointment."""
        base = self.settings.public_url
        return await self.gateway.start(
            await self._gateway_account(record.workspace_id),
            Checkout(
                order_id=record.id,
                amount=record.due,
                currency=record.currency,
                what=f"{record.PAY_FOR[4:].capitalize()} for {record.code}",  # minus "the "
                name=record.name,
                phone=record.phone,
                address=getattr(record, "address", ""),
                back_url=f"{base}/pay/{record.id}/back",
                notify_url=base + NOTIFY_PATH.replace("{record_id}", record.id),
            ),
        )

    async def settle(self, record_id: str, payment_id: str) -> Payable | None:
        """Checks a payment the gateway posted back for a row and, if it paid that unpaid row
        in full, places it. The visitor's return and the gateway's notice both come here; only
        the first one changes anything. Returns the row."""
        record = await self.find(record_id)
        if record is None:
            return None
        paid = await self.gateway.check(
            await self._gateway_account(record.workspace_id), payment_id
        )
        if paid is None or paid.order_id != record.id:
            return record
        if (paid.amount, paid.currency) != (record.due, record.currency):
            log.warning("payment amount differs", extra={"record_id": record.id})
            return record

        table = type(record)
        async with self.sessions() as s, s.begin():
            moved = await s.execute(  # only from unpaid, so two posts can't both place it
                update(table)
                .where(table.id == record.id, table.status == UNPAID)
                .values(
                    status=FIRST_STATUS,
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
            await self._placed(record, amount)
        elif record.status == "cancelled" and not record.paid:
            await self._alert(
                record,
                f"⚠️ {record.code} was paid after it was cancelled: refund {amount} "
                f"({paid.reference}).",
            )
        return record

    async def _placed(self, record: Payable, amount: str) -> None:
        if record.conversation_id:
            await self.team.tell_visitor(
                record.conversation_id,
                f"✅ Payment received: {amount} for {record.PAY_FOR}. {record.after()}",
            )
        await self._alert(
            record,
            f"💳 New {record.code}: {record.PAY_FOR} paid online, {amount}\n"
            f"{record.about()}; name {record.name}; phone {record.phone}",
        )

    async def _alert(self, record: Payable, text: str) -> None:
        if record.conversation_id is None:
            return
        async with self.sessions() as s:
            conv = await s.scalar(
                select(Conversation).where(
                    Conversation.id == record.conversation_id,
                    Conversation.workspace_id == record.workspace_id,
                )
            )
        if conv is not None:
            await self.team.alert(conv, text)
