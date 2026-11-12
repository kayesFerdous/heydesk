"""Paying HeyDesk for a plan. The owner's billing page reads `/api/billing` and
pays with `/api/billing/checkout`, which returns our SSLCommerz or bKash page. SSLCommerz sends
the owner back to `/billing/<id>/back` and tells the server itself at NOTIFY_PATH; only the
`val_id` it posts counts, checked with our store. bKash sends them back to `/billing/<id>/bkash`;
only its `paymentID`, executed with our bKash account, counts. Both returns send the owner on
to the billing page, `/billing?payment=paid|review|failed`, in the same tab. Owners only: a moderator gets a
403 (`owners_only`). `/api/billing/quote` shows what a plan costs before paying: an upgrade
is the difference for the days left, and a smaller plan waits until this one ends.
"""

from contextlib import suppress
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Form, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from heydesk.api.deps import Svc, WorkspaceId
from heydesk.models import SubscriptionPayment
from heydesk.ports.payments import PaymentUnavailable, Provider
from heydesk.services.billing import NOTIFY_PATH, PLANS

router = APIRouter(tags=["billing"])

type PlanKey = Literal["starter", "growth", "pro"]  # what can be bought; not the trial


class PlanOut(BaseModel):
    key: str
    name: str
    price: int  # a month, in poisha
    year_price: int  # twelve months for the price of ten
    replies: int  # AI replies in any 30 days
    moderators: int


class PaymentOut(BaseModel):
    id: str
    plan: str
    months: int
    kind: Literal["months", "upgrade"]
    amount: int  # poisha
    status: Literal["paid", "review"]  # review: paid, not applied yet; HeyDesk sorts it out
    paid_at: datetime | None


class QuoteOut(BaseModel):
    kind: Literal["months", "upgrade"]  # upgrade: the difference for the days left, from now
    amount: int  # poisha: what the Pay button charges
    paid_until: datetime  # the plan's last paid day after paying


class BillingOut(BaseModel):
    plan: str
    paid_until: datetime
    active: bool  # False: the agent isn't answering until a plan is paid
    replies_used: int  # in the last 30 days
    replies: int
    moderators: int
    plans: list[PlanOut]
    gateways: list[Provider]  # a Pay button for each; empty: paying online isn't set up
    payments: list[PaymentOut]  # the receipts, newest first


class CheckoutIn(BaseModel):
    plan: PlanKey
    months: Literal[1, 12] = 1
    gateway: Provider = "sslcommerz"


class CheckoutOut(BaseModel):
    url: str  # the gateway's page: send the owner there


@router.get("/api/billing")
async def get_billing(workspace_id: WorkspaceId, services: Svc) -> BillingOut:
    """The business's plan, how much of it is used, and the plans it can buy."""
    b = await services.billing.billing(workspace_id)
    plans = [
        PlanOut(
            key=key,
            name=p.name,
            price=p.price,
            year_price=p.cost(12),
            replies=p.replies,
            moderators=p.moderators,
        )
        for key, p in PLANS.items()
        if key != "trial"
    ]
    return BillingOut(
        plan=b.plan,
        paid_until=b.paid_until,
        active=b.active,
        replies_used=b.replies_used,
        replies=b.replies,
        moderators=b.moderators,
        plans=plans,
        gateways=b.gateways,
        payments=[PaymentOut.model_validate(p, from_attributes=True) for p in b.payments],
    )


@router.get("/api/billing/quote")
async def get_quote(
    workspace_id: WorkspaceId, services: Svc, plan: PlanKey, months: Literal[1, 12] = 1
) -> QuoteOut:
    """What paying for `plan` costs now; a 409 says why it can't be bought yet."""
    q = await services.billing.quote(workspace_id, plan, months)
    return QuoteOut(kind=q.kind, amount=q.amount, paid_until=q.paid_until)


@router.post("/api/billing/checkout")
async def checkout(body: CheckoutIn, workspace_id: WorkspaceId, services: Svc) -> CheckoutOut:
    """Starts paying for a plan; a 400 says why it can't (no store set up, the gateway down)."""
    url = await services.billing.checkout(workspace_id, body.plan, body.months, body.gateway)
    return CheckoutOut(url=url)


def to_billing_page(payment: SubscriptionPayment | None, services: Svc) -> RedirectResponse:
    """Back to the owner's billing page in the same tab, which says how it went: `paid`,
    `review` (paid, not applied) or `failed`. 303, so SSLCommerz's POST becomes a GET."""
    outcome = payment.status if payment and payment.status != "unpaid" else "failed"
    url = f"{services.billing.settings.public_url}/billing?payment={outcome}"
    return RedirectResponse(url, status_code=303)


@router.post("/billing/{payment_id}/back", include_in_schema=False)
async def back(
    payment_id: str, services: Svc, val_id: Annotated[str, Form()] = ""
) -> RedirectResponse:
    """SSLCommerz sends the owner back here, paid, failed or cancelled."""
    payment = None
    if val_id:
        with suppress(PaymentUnavailable):  # then the gateway's own notice settles it
            payment = await services.billing.check_payment(payment_id, val_id)
    return to_billing_page(payment, services)


@router.get("/billing/{payment_id}/bkash", include_in_schema=False)
async def bkash_back(
    payment_id: str,
    services: Svc,
    bkash_id: Annotated[str, Query(alias="paymentID")] = "",
    outcome: Annotated[str, Query(alias="status")] = "",
) -> RedirectResponse:
    """bKash sends the owner back here, paid, failed or cancelled. Only a "success" is
    worth asking bKash about, and only bKash's answer counts."""
    payment = None
    if bkash_id and outcome == "success":
        with suppress(PaymentUnavailable):
            payment = await services.billing.check_payment(payment_id, bkash_id, "bkash")
    return to_billing_page(payment, services)


@router.post(NOTIFY_PATH, include_in_schema=False)
async def notice(
    payment_id: str, services: Svc, val_id: Annotated[str, Form()] = ""
) -> dict[str, bool]:
    """The gateway's notice (SSLCommerz's IPN), even if the owner closed the tab."""
    if val_id:
        await services.billing.check_payment(payment_id, val_id)
    return {"ok": True}
