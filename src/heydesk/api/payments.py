"""Payments. The owner connects the business's own SSLCommerz store at
`/api/agent/sslcommerz` and its bKash account at `/api/agent/bkash`, one or both. The
visitor's pages need no login: the order's or appointment's id is the key. `/pay/<id>` sends
them to the gateway, or with both connected lets them pick one.

SSLCommerz sends them back to `/pay/<id>/back`, and tells the server itself at NOTIFY_PATH;
only the `val_id` it posts counts, checked with the business's store. bKash sends them back
to `/pay/<id>/bkash`; only its `paymentID`, executed with the business's bKash account, counts.
"""

import html
from contextlib import suppress
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Form, Query, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field

from heydesk.api.deps import Svc, Workspace
from heydesk.errors import NotFound
from heydesk.models.customer_request import UNPAID
from heydesk.ports.payments import Account, PaymentUnavailable, Provider
from heydesk.runtime.speech import money
from heydesk.services.payments import NOTIFY_PATH, Payable

router = APIRouter(tags=["payments"])


class SslCommerzIn(BaseModel):
    store_id: str = Field(min_length=1, max_length=100, description="From SSLCommerz's email")
    password: str = Field(
        min_length=1, max_length=200, description="The store password: the API key, not the login"
    )
    sandbox: bool = Field(default=True, description="SSLCommerz's test mode: no real money")


class SslCommerzOut(BaseModel):
    store_id: str
    sandbox: bool
    connected_at: datetime  # the password never comes back


@router.get("/api/agent/sslcommerz")
async def get_sslcommerz(ws: Workspace, services: Svc) -> SslCommerzOut:
    """The business's SSLCommerz store, or 404 when none is connected."""
    saved = await services.payments.account(ws, "sslcommerz")
    if saved is None:
        raise NotFound("No SSLCommerz account is connected.")
    return SslCommerzOut(
        store_id=saved.store_id, sandbox=saved.sandbox, connected_at=saved.created_at
    )


@router.put("/api/agent/sslcommerz")
async def connect_sslcommerz(body: SslCommerzIn, ws: Workspace, services: Svc) -> SslCommerzOut:
    """Checks the store with SSLCommerz (a 400 says why it said no), then keeps it. From the
    next message, an agent with paying first on asks for payments into it."""
    account = Account(body.store_id.strip(), body.password.strip(), body.sandbox)
    saved = await services.payments.connect(ws, "sslcommerz", account)
    return SslCommerzOut(
        store_id=saved.store_id, sandbox=saved.sandbox, connected_at=saved.created_at
    )


@router.delete("/api/agent/sslcommerz", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_sslcommerz(ws: Workspace, services: Svc) -> None:
    await services.payments.disconnect(ws, "sslcommerz")


class BkashIn(BaseModel):
    app_key: str = Field(min_length=1, max_length=100, description="From bKash's merchant email")
    app_secret: str = Field(min_length=1, max_length=200)
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=200)
    sandbox: bool = Field(default=True, description="bKash's test mode: no real money")


class BkashOut(BaseModel):
    username: str
    sandbox: bool
    connected_at: datetime  # the secret and the password never come back


@router.get("/api/agent/bkash")
async def get_bkash(ws: Workspace, services: Svc) -> BkashOut:
    """The business's bKash account, or 404 when none is connected."""
    saved = await services.payments.account(ws, "bkash")
    if saved is None:
        raise NotFound("No bKash account is connected.")
    return BkashOut(username=saved.username, sandbox=saved.sandbox, connected_at=saved.created_at)


@router.put("/api/agent/bkash")
async def connect_bkash(body: BkashIn, ws: Workspace, services: Svc) -> BkashOut:
    """Checks the keys with bKash by asking it for a token (a 400 says why it said no), then
    keeps them. From the next payment link, the visitor can pay by bKash."""
    account = Account(
        store_id="",
        password=body.password.strip(),
        sandbox=body.sandbox,
        app_key=body.app_key.strip(),
        app_secret=body.app_secret.strip(),
        username=body.username.strip(),
    )
    saved = await services.payments.connect(ws, "bkash", account)
    return BkashOut(username=saved.username, sandbox=saved.sandbox, connected_at=saved.created_at)


@router.delete("/api/agent/bkash", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_bkash(ws: Workspace, services: Svc) -> None:
    await services.payments.disconnect(ws, "bkash")


NOT_PAID = "The payment didn't go through. Open the payment link again to try once more."


HEAD = (
    '<!doctype html><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width, initial-scale=1">'
    "<title>Payment</title>"
)
STYLE = "font: 18px system-ui; max-width: 32em; margin: 3em auto; padding: 0 1em"
BUTTON = (
    "display: block; margin: 1em 0; padding: .8em; border-radius: 8px; text-align: center;"
    "color: #fff; text-decoration: none; background: "
)
COLOURS = {"bkash": "#e2136e", "sslcommerz": "#1f5fbf"}
LABELS = {"bkash": "Pay with bKash", "sslcommerz": "Pay with a card or mobile banking"}


def page(text: str, status_code: int = 200) -> HTMLResponse:
    body = (
        f'{HEAD}<p style="{STYLE}">{html.escape(text, quote=False)}<br><br>'
        "You can close this page and go back to the chat.</p>"
    )
    return HTMLResponse(body, status_code)


def choose_page(record: Payable, providers: list[Provider]) -> HTMLResponse:
    """One button for each gateway the business is connected to."""
    what = f"{record.PAY_FOR[4:].capitalize()} for {record.code}: {money(record.amount_due, record.currency)}"
    buttons = "".join(
        f'<a style="{BUTTON}{COLOURS[p]}" href="/pay/{record.id}?via={p}">{LABELS[p]}</a>'
        for p in providers
    )
    return HTMLResponse(f'{HEAD}<div style="{STYLE}"><p>{html.escape(what)}</p>{buttons}</div>')


def record_page(record: Payable | None) -> HTMLResponse:
    if record is None:
        return page("There's nothing to pay for here.", 404)
    if record.status == UNPAID:
        return page(NOT_PAID)
    if record.status == "cancelled":
        return page(f"{record.code} was cancelled.")
    return page(f"Payment received. {record.after()}")


@router.get("/pay/{record_id}", response_model=None, include_in_schema=False)
async def pay(
    record_id: str, services: Svc, via: Provider | None = None
) -> HTMLResponse | RedirectResponse:
    """The link the agent sends: off to the gateway to pay. With both connected, the visitor
    picks one first, and comes back here with `via`."""
    record = await services.payments.find(record_id)
    if record is None or record.status != UNPAID:
        return record_page(record)
    providers = await services.payments.providers(record.workspace_id)
    if via is None and len(providers) > 1:
        return choose_page(record, providers)
    provider = via or (providers[0] if providers else "sslcommerz")
    try:
        url = await services.payments.start(record, provider)
        return RedirectResponse(url, status_code=303)
    except PaymentUnavailable:
        return page("The payment page isn't answering. Please try the link again in a minute.")


@router.post("/pay/{record_id}/back", include_in_schema=False)
async def back(record_id: str, services: Svc, val_id: Annotated[str, Form()] = "") -> HTMLResponse:
    """The gateway sends the visitor back here, paid, failed or cancelled. Only `val_id`,
    checked with the gateway, counts; what else it posts could be forged."""
    if val_id:
        with suppress(PaymentUnavailable):  # then the gateway's own notice settles it
            await services.payments.settle(record_id, val_id)
    return record_page(await services.payments.find(record_id))


@router.get("/pay/{record_id}/bkash", include_in_schema=False)
async def bkash_back(
    record_id: str,
    services: Svc,
    payment_id: Annotated[str, Query(alias="paymentID")] = "",
    outcome: Annotated[str, Query(alias="status")] = "",
) -> HTMLResponse:
    """bKash sends the visitor back here, paid, failed or cancelled. Only a "success" is worth
    asking bKash about, and only bKash's answer counts: the link itself could be forged."""
    if payment_id and outcome == "success":
        with suppress(PaymentUnavailable):
            await services.payments.settle(record_id, payment_id, "bkash")
    return record_page(await services.payments.find(record_id))


@router.post(NOTIFY_PATH, include_in_schema=False)
async def notice(
    record_id: str, services: Svc, val_id: Annotated[str, Form()] = ""
) -> dict[str, bool]:
    """The gateway's notice (SSLCommerz's IPN), even if the visitor closed the tab."""
    if val_id:
        await services.payments.settle(record_id, val_id)
    return {"ok": True}
