"""The visitor's payment pages (D47): no login, the order's or appointment's id is the key.

`/pay/<id>` sends them to the gateway; the gateway sends them back to `/pay/<id>/back`, and
tells the server itself at NOTIFY_PATH. Only the `val_id` it posts counts, checked with it.
"""

import html
from contextlib import suppress
from typing import Annotated

from fastapi import APIRouter, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from heydesk.api.deps import Svc
from heydesk.models.taken import UNPAID
from heydesk.ports.payments import PaymentUnavailable
from heydesk.services.payments import NOTIFY_PATH, Payable

router = APIRouter(tags=["payments"], include_in_schema=False)

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


@router.get("/pay/{record_id}", response_model=None)
async def pay(record_id: str, services: Svc) -> HTMLResponse | RedirectResponse:
    """The link the agent sends: off to the gateway to pay."""
    record = await services.payments.find(record_id)
    if record is None or record.status != UNPAID:
        return record_page(record)
    try:
        return RedirectResponse(await services.payments.start(record), status_code=303)
    except PaymentUnavailable:
        return page("The payment page isn't answering. Please try the link again in a minute.")


@router.post("/pay/{record_id}/back")
async def back(record_id: str, services: Svc, val_id: Annotated[str, Form()] = "") -> HTMLResponse:
    """The gateway sends the visitor back here, paid, failed or cancelled. Only `val_id`,
    checked with the gateway, counts; what else it posts could be forged."""
    if val_id:
        with suppress(PaymentUnavailable):  # then the gateway's own notice settles it
            await services.payments.settle(val_id)
    return record_page(await services.payments.find(record_id))


@router.post(NOTIFY_PATH)
async def notice(services: Svc, val_id: Annotated[str, Form()] = "") -> dict[str, bool]:
    """The gateway's notice (SSLCommerz's IPN), even if the visitor closed the tab."""
    if val_id:
        await services.payments.settle(val_id)
    return {"ok": True}
