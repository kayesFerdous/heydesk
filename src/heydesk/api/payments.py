"""Payments (D47-D49). The owner connects the business's own SSLCommerz store at
`/api/agent/sslcommerz`. The visitor's pages need no login: the order's or appointment's id
is the key. `/pay/<id>` sends them to the gateway; the gateway sends them back to
`/pay/<id>/back`, and tells the server itself at NOTIFY_PATH. Only the `val_id` it posts
counts, checked with the business's store.
"""

import html
from contextlib import suppress
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Form, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field

from heydesk.api.deps import Svc, Workspace
from heydesk.errors import NotFound
from heydesk.models.taken import UNPAID
from heydesk.ports.payments import PaymentUnavailable
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
    saved = await services.payments.account(ws)
    if saved is None:
        raise NotFound("No SSLCommerz account is connected.")
    return SslCommerzOut(
        store_id=saved.store_id, sandbox=saved.sandbox, connected_at=saved.created_at
    )


@router.put("/api/agent/sslcommerz")
async def connect_sslcommerz(body: SslCommerzIn, ws: Workspace, services: Svc) -> SslCommerzOut:
    """Checks the store with SSLCommerz (a 400 says why it said no), then keeps it. From the
    next message, an agent with paying first on asks for payments into it."""
    saved = await services.payments.connect(
        ws, body.store_id.strip(), body.password.strip(), body.sandbox
    )
    return SslCommerzOut(
        store_id=saved.store_id, sandbox=saved.sandbox, connected_at=saved.created_at
    )


@router.delete("/api/agent/sslcommerz", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_sslcommerz(ws: Workspace, services: Svc) -> None:
    await services.payments.disconnect(ws)


NOT_PAID = "The payment didn't go through. Open the payment link again to try once more."


def page(text: str, status_code: int = 200) -> HTMLResponse:
    body = (
        '<!doctype html><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>Payment</title>"
        '<p style="font: 18px system-ui; max-width: 32em; margin: 3em auto; padding: 0 1em">'
        f"{html.escape(text, quote=False)}<br><br>"
        "You can close this page and go back to the chat.</p>"
    )
    return HTMLResponse(body, status_code)


def record_page(record: Payable | None) -> HTMLResponse:
    if record is None:
        return page("There's nothing to pay for here.", 404)
    if record.status == UNPAID:
        return page(NOT_PAID)
    if record.status == "cancelled":
        return page(f"{record.code} was cancelled.")
    return page(f"Payment received. {record.after()}")


@router.get("/pay/{record_id}", response_model=None, include_in_schema=False)
async def pay(record_id: str, services: Svc) -> HTMLResponse | RedirectResponse:
    """The link the agent sends: off to the gateway to pay."""
    record = await services.payments.find(record_id)
    if record is None or record.status != UNPAID:
        return record_page(record)
    try:
        return RedirectResponse(await services.payments.start(record), status_code=303)
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


@router.post(NOTIFY_PATH, include_in_schema=False)
async def notice(
    record_id: str, services: Svc, val_id: Annotated[str, Form()] = ""
) -> dict[str, bool]:
    """The gateway's notice (SSLCommerz's IPN), even if the visitor closed the tab."""
    if val_id:
        await services.payments.settle(record_id, val_id)
    return {"ok": True}
