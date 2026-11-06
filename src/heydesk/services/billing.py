"""Plans and paying for HeyDesk. A business is on one plan until `paid_until`, and a
plan is a number of AI replies in any 30 days plus a number of moderators.

A new business gets the free trial: `TRIAL_DAYS` and `PLANS["trial"]`'s replies. The owner
pays for a plan on the billing page, through **our** SSLCommerz store (`BILLING_STORE_*`) or
our bKash account (`BILLING_BKASH_*`), not the business's own ones. The same check
as an order's payment applies: only the gateway's word counts, for this payment and this
amount. A paid month adds 30 days, a paid year 365, from today or from when the plan would have
ended, whichever is later. An admin can also set a plan by hand, for a payment made another way.

Changing plans. The plan is prepaid, so nothing renews by itself, and `quote` prices
every purchase from where the business stands:
- on the trial, or after the paid plan has ended: the plan's full price, starting now;
- the same plan while it runs: its full price, added after the days left;
- a bigger plan while one runs: an **upgrade**, only the difference for the days left (at the
  rate of the cycle paid last), switched at once, ending on the same day;
- a smaller plan while one runs: refused until it ends (it's paid for); and never with more
  moderators than the smaller plan has.
`check_payment` prices the payment again when the gateway says it's paid: if the plan changed while
the owner was paying (another payment, an admin), the payment is kept as `review` and an admin
settles it by hand, rather than applying a price that no longer holds.

A plan that has ended keeps answering for `GRACE_DAYS` (not a trial); then the agent stops,
and visitors are told how to reach the business (`VisitorService`).
"""

import logging
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from heydesk.config import Settings
from heydesk.errors import Conflict, HeyDeskError, NotFound
from heydesk.models import SubscriptionPayment, Turn, User, Workspace
from heydesk.ports.payments import (
    PROVIDERS,
    Account,
    Checkout,
    Gateways,
    PaymentUnavailable,
    Provider,
)

log = logging.getLogger(__name__)

TRIAL_DAYS = 14
GRACE_DAYS = 3
WINDOW_DAYS = 30  # replies are counted over the last 30 days
DAYS_PER_PLAN = {1: 30, 12: 365}  # months bought → days added
MIN_CHARGE = 1_000  # ৳10, the least SSLCommerz takes
NOTIFY_PATH = "/api/billing/sslcommerz/{payment_id}"  # SSLCommerz's own post; bKash has none


@dataclass(frozen=True)
class Plan:
    name: str
    price: int  # a month, in minor units (poisha)
    replies: int  # AI replies in any 30 days
    moderators: int

    def cost(self, months: int) -> int:
        """A year is ten months' price."""
        return self.price * (10 if months == 12 else months)


PLANS = {
    "trial": Plan("Free trial", 0, 200, 1),
    "starter": Plan("Starter", 249_000, 3_000, 1),
    "growth": Plan("Growth", 599_000, 12_000, 3),
    "pro": Plan("Pro", 1_299_000, 30_000, 10),
}


@dataclass(frozen=True)
class Quote:
    """What paying for a plan costs now, and what it gives."""

    kind: Literal["months", "upgrade"]
    amount: int  # poisha
    paid_until: datetime  # after paying


def quote(workspace: Workspace, plan: str, months: int, moderators: int, now: datetime) -> Quote:
    """Prices `months` of `plan` for this business now; a `Conflict` says why it can't buy it."""
    new, current = PLANS[plan], PLANS[workspace.plan]
    running = workspace.plan != "trial" and now < workspace.paid_until
    if running and plan != workspace.plan:
        if new.price < current.price:
            raise Conflict(
                f"{current.name} is paid until {workspace.paid_until:%d %b %Y}. "
                f"You can move to {new.name} once it ends."
            )
        days_left = math.ceil((workspace.paid_until - now) / timedelta(days=1))
        per_day = (new.cost(workspace.months) - current.cost(workspace.months)) / DAYS_PER_PLAN[
            workspace.months
        ]
        amount = math.ceil(per_day * days_left / 100) * 100  # whole taka, rounded up
        return Quote("upgrade", max(amount, MIN_CHARGE), workspace.paid_until)
    if moderators > new.moderators:
        extra = moderators - new.moderators
        raise Conflict(
            f"{new.name} has {new.moderators} moderator{'s' if new.moderators > 1 else ''}. "
            f"Remove {extra} from your team first."
        )
    start = workspace.paid_until if running else now  # running here means the same plan
    return Quote("months", new.cost(months), start + timedelta(days=DAYS_PER_PLAN[months]))


async def count_moderators(s: AsyncSession, workspace_id: str) -> int:
    query = select(func.count()).select_from(User)
    query = query.where(User.workspace_id == workspace_id, User.role == "moderator")
    return await s.scalar(query) or 0


@dataclass(frozen=True)
class Billing:
    """What the owner's billing page shows."""

    plan: str
    paid_until: datetime
    active: bool  # False: the agent isn't answering
    replies_used: int  # in the last 30 days
    replies: int  # the plan's limit
    moderators: int
    gateways: list[Provider]  # the Pay buttons: ours that are set up
    payments: list[SubscriptionPayment]  # paid or held for review, newest first


def plan_end_date(workspace: Workspace) -> datetime:
    """When the agent stops: the paid date, plus the grace days for a paid plan."""
    return workspace.paid_until + timedelta(days=0 if workspace.plan == "trial" else GRACE_DAYS)


async def replies_used(s: AsyncSession, workspace_id: str, now: datetime) -> int:
    since = now - timedelta(days=WINDOW_DAYS)
    used = await s.scalar(
        select(func.count())
        .select_from(Turn)
        .where(Turn.workspace_id == workspace_id, Turn.at > since)
    )
    return used or 0


async def plan_problem(s: AsyncSession, workspace_id: str, now: datetime) -> str | None:
    """Why the business's plan allows no turn now, or None. For `VisitorService`."""
    workspace = await s.get(Workspace, workspace_id)
    if workspace is None:
        return None
    if now >= plan_end_date(workspace):
        return "plan_ended"
    if await replies_used(s, workspace_id, now) >= PLANS[workspace.plan].replies:
        return "plan_replies"
    return None


def new_workspace(name: str, now: datetime) -> Workspace:
    """A business signing up: on the free trial."""
    return Workspace(
        name=name, plan="trial", paid_until=now + timedelta(days=TRIAL_DAYS), created_at=now
    )


def dev_workspace(workspace_id: str, now: datetime) -> Workspace:
    """The dev workspace isn't a customer: Pro for ten years, so the dev chat and the demo
    never stop."""
    return Workspace(
        id=workspace_id,
        name="Dev",
        plan="pro",
        paid_until=now + timedelta(days=3650),
        created_at=now,
    )


class BillingService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        gateways: Gateways,
        settings: Settings,
        now: Callable[[], datetime],
    ) -> None:
        self.sessions = sessions
        self.gateways = gateways
        self.settings = settings
        self.now = now

    async def billing(self, workspace_id: str) -> Billing:
        now = self.now()
        async with self.sessions() as s:
            workspace = await s.get(Workspace, workspace_id)
            if workspace is None:
                raise NotFound("There's no such business.")
            used = await replies_used(s, workspace_id, now)
            payments = await s.scalars(
                select(SubscriptionPayment)
                .where(
                    SubscriptionPayment.workspace_id == workspace_id,
                    SubscriptionPayment.status != "unpaid",
                )
                .order_by(SubscriptionPayment.paid_at.desc())
            )
        plan = PLANS[workspace.plan]
        return Billing(
            workspace.plan,
            workspace.paid_until,
            now < plan_end_date(workspace),
            used,
            plan.replies,
            plan.moderators,
            self.set_up(),
            list(payments),
        )

    def set_up(self) -> list[Provider]:
        """Our gateways that have their keys in the settings."""
        s = self.settings
        keys = {"sslcommerz": s.BILLING_STORE_ID, "bkash": s.BILLING_BKASH_APP_KEY}
        return [p for p in PROVIDERS if keys[p]]

    async def quote(self, workspace_id: str, plan: str, months: int) -> Quote:
        async with self.sessions() as s:
            workspace = await s.get(Workspace, workspace_id)
            if workspace is None:
                raise NotFound("There's no such business.")
            return quote(
                workspace, plan, months, await count_moderators(s, workspace_id), self.now()
            )

    async def checkout(
        self, workspace_id: str, plan: str, months: int, gateway: Provider = "sslcommerz"
    ) -> str:
        """The gateway's page to pay for `months` of `plan`."""
        if gateway not in self.set_up():
            raise HeyDeskError("Paying online isn't set up yet. Please contact HeyDesk to pay.")
        q = await self.quote(workspace_id, plan, months)
        payment = SubscriptionPayment(
            workspace_id=workspace_id,
            plan=plan,
            months=months,
            kind=q.kind,
            amount=q.amount,
            created_at=self.now(),
        )
        what = "upgrade" if q.kind == "upgrade" else f"{months} month{'s' if months > 1 else ''}"
        async with self.sessions() as s, s.begin():
            s.add(payment)
        base = self.settings.public_url
        back = "back" if gateway == "sslcommerz" else "bkash"
        try:
            return await self.gateways[gateway].start(
                self._account(gateway),
                Checkout(
                    order_id=payment.id,
                    amount=payment.amount,
                    currency="BDT",
                    what=f"HeyDesk {PLANS[plan].name}, {what}",
                    name="HeyDesk customer",
                    phone="01700000000",  # SSLCommerz needs one; the owner types theirs there
                    address="",
                    back_url=f"{base}/billing/{payment.id}/{back}",
                    notify_url=base + NOTIFY_PATH.replace("{payment_id}", payment.id),
                ),
            )
        except PaymentUnavailable as error:
            raise HeyDeskError(f"The payment page isn't answering: {error}") from None

    async def check_payment(
        self, payment_id: str, val_id: str, gateway: Provider = "sslcommerz"
    ) -> SubscriptionPayment | None:
        """Checks a payment a gateway sent word of (SSLCommerz's `val_id`, bKash's paymentID)
        and, if it paid this payment in full, moves the business onto the plan. Only the first
        word changes anything. Priced again now: a plan that changed meanwhile holds it for
        review instead."""
        async with self.sessions() as s:
            payment = await s.get(SubscriptionPayment, payment_id)
        if payment is None:
            return None
        paid = await self.gateways[gateway].check(self._account(gateway), val_id)
        if paid is None or paid.order_id != payment.id:
            return payment
        if (paid.amount, paid.currency) != (payment.amount, "BDT"):
            log.warning("billing amount differs", extra={"payment_id": payment.id})
            return payment
        now = self.now()
        async with self.sessions() as s, s.begin():
            # locked, so two payments settling at once can't both start from the same day
            workspace = await s.get(Workspace, payment.workspace_id, with_for_update=True)
            assert workspace is not None
            try:
                q: Quote | None = quote(
                    workspace,
                    payment.plan,
                    payment.months,
                    await count_moderators(s, workspace.id),
                    now,
                )
            except Conflict:
                q = None
            fits = q is not None and q.kind == payment.kind and q.amount <= payment.amount
            moved = await s.execute(  # only from unpaid, so two posts can't both extend it
                update(SubscriptionPayment)
                .where(SubscriptionPayment.id == payment.id, SubscriptionPayment.status == "unpaid")
                .values(
                    status="paid" if fits else "review", payment_ref=paid.reference, paid_at=now
                )
            )
            if moved.rowcount == 1 and q is not None and fits:  # type: ignore[attr-defined]
                workspace.plan, workspace.paid_until = payment.plan, q.paid_until
                if q.kind == "months":
                    workspace.months = payment.months
                log.info("plan paid", extra={"workspace_id": workspace.id, "plan": payment.plan})
            elif moved.rowcount == 1:  # type: ignore[attr-defined]
                log.warning("plan payment held for review", extra={"payment_id": payment.id})
        async with self.sessions() as s:
            return await s.get(SubscriptionPayment, payment.id)

    async def set_plan(self, workspace_id: str, plan: str, paid_until: datetime) -> None:
        """An admin's change, for a payment made another way."""
        async with self.sessions() as s, s.begin():
            workspace = await s.get(Workspace, workspace_id)
            if workspace is None:
                raise NotFound("There's no such business.")
            workspace.plan, workspace.paid_until = plan, paid_until

    def _account(self, gateway: Provider) -> Account:
        s = self.settings
        if gateway == "sslcommerz":
            return Account(s.BILLING_STORE_ID, s.BILLING_STORE_PASSWORD, s.BILLING_SANDBOX)
        return Account(
            store_id="",
            password=s.BILLING_BKASH_PASSWORD,
            sandbox=s.BILLING_BKASH_SANDBOX,
            app_key=s.BILLING_BKASH_APP_KEY,
            app_secret=s.BILLING_BKASH_APP_SECRET,
            username=s.BILLING_BKASH_USERNAME,
        )
