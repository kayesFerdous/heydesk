"""Payments. The owner connects the business's own SSLCommerz store at
`/api/agent/sslcommerz` and its bKash account at `/api/agent/bkash`, one or both. The
visitor's pages need no login: the order's id is the key. `/pay/<id>` sends
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

from heydesk.api.deps import Svc, WorkspaceId
from heydesk.errors import NotFound
from heydesk.models.record import UNPAID
from heydesk.ports.payments import GatewayAccount, PaymentUnavailable, Provider
from heydesk.runtime.speech import money
from heydesk.services.payments import NOTIFY_PATH
from heydesk.shop.models import ShopOrder

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
async def get_sslcommerz(workspace_id: WorkspaceId, services: Svc) -> SslCommerzOut:
    """The business's SSLCommerz store, or 404 when none is connected."""
    saved = await services.payments.account(workspace_id, "sslcommerz")
    if saved is None:
        raise NotFound("No SSLCommerz account is connected.")
    return SslCommerzOut(
        store_id=saved.store_id, sandbox=saved.sandbox, connected_at=saved.created_at
    )


@router.put("/api/agent/sslcommerz")
async def connect_sslcommerz(
    body: SslCommerzIn, workspace_id: WorkspaceId, services: Svc
) -> SslCommerzOut:
    """Checks the store with SSLCommerz (a 400 says why it said no), then keeps it. From the
    next message, an agent with paying first on asks for payments into it."""
    account = GatewayAccount(body.store_id.strip(), body.password.strip(), body.sandbox)
    saved = await services.payments.connect(workspace_id, "sslcommerz", account)
    return SslCommerzOut(
        store_id=saved.store_id, sandbox=saved.sandbox, connected_at=saved.created_at
    )


@router.delete("/api/agent/sslcommerz", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_sslcommerz(workspace_id: WorkspaceId, services: Svc) -> None:
    await services.payments.disconnect(workspace_id, "sslcommerz")


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
async def get_bkash(workspace_id: WorkspaceId, services: Svc) -> BkashOut:
    """The business's bKash account, or 404 when none is connected."""
    saved = await services.payments.account(workspace_id, "bkash")
    if saved is None:
        raise NotFound("No bKash account is connected.")
    return BkashOut(username=saved.username, sandbox=saved.sandbox, connected_at=saved.created_at)


@router.put("/api/agent/bkash")
async def connect_bkash(body: BkashIn, workspace_id: WorkspaceId, services: Svc) -> BkashOut:
    """Checks the keys with bKash by asking it for a token (a 400 says why it said no), then
    keeps them. From the next payment link, the visitor can pay by bKash."""
    account = GatewayAccount(
        store_id="",
        password=body.password.strip(),
        sandbox=body.sandbox,
        app_key=body.app_key.strip(),
        app_secret=body.app_secret.strip(),
        username=body.username.strip(),
    )
    saved = await services.payments.connect(workspace_id, "bkash", account)
    return BkashOut(username=saved.username, sandbox=saved.sandbox, connected_at=saved.created_at)


@router.delete("/api/agent/bkash", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_bkash(workspace_id: WorkspaceId, services: Svc) -> None:
    await services.payments.disconnect(workspace_id, "bkash")


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


def html_page(text: str, status_code: int = 200) -> HTMLResponse:
    body = (
        f'{HEAD}<p style="{STYLE}">{html.escape(text, quote=False)}<br><br>'
        "You can close this page and go back to the chat.</p>"
    )
    return HTMLResponse(body, status_code)


def choose_page(order: ShopOrder, providers: list[Provider]) -> HTMLResponse:
    """One button for each gateway the business is connected to."""
    what = f"{order.PAY_FOR[4:].capitalize()} for {order.code}: {money(order.amount_due, order.currency)}"
    buttons = "".join(
        f'<a style="{BUTTON}{COLOURS[p]}" href="/pay/{order.id}?via={p}">{LABELS[p]}</a>'
        for p in providers
    )
    return HTMLResponse(f'{HEAD}<div style="{STYLE}"><p>{html.escape(what)}</p>{buttons}</div>')


def result_page(order: ShopOrder | None) -> HTMLResponse:
    if order is None:
        return html_page("There's nothing to pay for here.", 404)
    if order.status == UNPAID:
        return html_page(NOT_PAID)
    if order.status == "cancelled":
        return html_page(f"{order.code} was cancelled.")
    return html_page(f"Payment received. {order.next_step_text()}")


@router.get("/pay/{order_id}", response_model=None, include_in_schema=False)
async def pay(
    order_id: str, services: Svc, via: Provider | None = None
) -> HTMLResponse | RedirectResponse:
    """The link the agent sends: off to the gateway to pay. With both connected, the visitor
    picks one first, and comes back here with `via`."""
    order = await services.payments.find(order_id)
    if order is None or order.status != UNPAID:
        return result_page(order)
    providers = await services.payments.providers(order.workspace_id)
    if via is None and len(providers) > 1:
        return choose_page(order, providers)
    provider = via or (providers[0] if providers else "sslcommerz")
    try:
        url = await services.payments.start(order, provider)
        return RedirectResponse(url, status_code=303)
    except PaymentUnavailable:
        return html_page("The payment page isn't answering. Please try the link again in a minute.")


@router.post("/pay/{order_id}/back", include_in_schema=False)
async def sslcommerz_return(
    order_id: str, services: Svc, val_id: Annotated[str, Form()] = ""
) -> HTMLResponse:
    """The gateway sends the visitor back here, paid, failed or cancelled. Only `val_id`,
    checked with the gateway, counts; what else it posts could be forged."""
    if val_id:
        with suppress(PaymentUnavailable):  # then the gateway's own notice settles it
            await services.payments.confirm_payment(order_id, val_id)
    return result_page(await services.payments.find(order_id))


@router.get("/pay/{order_id}/bkash", include_in_schema=False)
async def bkash_return(
    order_id: str,
    services: Svc,
    payment_id: Annotated[str, Query(alias="paymentID")] = "",
    outcome: Annotated[str, Query(alias="status")] = "",
) -> HTMLResponse:
    """bKash sends the visitor back here, paid, failed or cancelled. Only a "success" is worth
    asking bKash about, and only bKash's answer counts: the link itself could be forged."""
    if payment_id and outcome == "success":
        with suppress(PaymentUnavailable):
            await services.payments.confirm_payment(order_id, payment_id, "bkash")
    return result_page(await services.payments.find(order_id))


@router.post(NOTIFY_PATH, include_in_schema=False)
async def sslcommerz_notice(
    order_id: str, services: Svc, val_id: Annotated[str, Form()] = ""
) -> dict[str, bool]:
    """The gateway's notice (SSLCommerz's IPN), even if the visitor closed the tab."""
    if val_id:
        await services.payments.confirm_payment(order_id, val_id)
    return {"ok": True}
