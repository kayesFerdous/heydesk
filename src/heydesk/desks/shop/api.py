"""A shop's own routes: its products, and the orders the agent took. And the buyer's
payment pages (D47), which need no login: the order's id is the key."""

import html
from contextlib import suppress
from datetime import datetime
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

from heydesk.api.deps import Paged, Svc, Workspace
from heydesk.desks.shop.models import ShopOrder
from heydesk.desks.shop.store import ShopStore
from heydesk.desks.shop.values import Product, products_from_csv
from heydesk.ports.payments import PaymentUnavailable
from heydesk.services.payments import NOTIFY_PATH

router = APIRouter(tags=["shop"])


async def shop_store(services: Svc, ws: Workspace) -> ShopStore:
    """404 unless the business's agent is a shop."""
    return cast(ShopStore, await services.agents.store(ws, "shop"))


type Shop = Annotated[ShopStore, Depends(shop_store)]


class ShopOrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    code: str  # what the buyer was told: "O-101"
    conversation_id: str | None
    status: str  # new → confirmed → shipped → delivered or returned; or cancelled
    name: str
    phone: str
    lines: list[dict[str, Any]]  # {code, name, size, colour, quantity, price}
    subtotal: int  # minor units
    delivery_charge: int
    total: int  # the whole order
    paid: int  # paid online first (D47); the rider collects `total - paid`
    payment_ref: str  # the payment gateway's id for it
    currency: str
    zone: str
    address: str
    note: str
    tracking: str
    created_at: datetime
    updated_at: datetime


class OrderChange(BaseModel):
    status: str | None = None  # where it may go depends on where it is: see the 409 message
    tracking: str | None = Field(
        default=None, max_length=200, description="The courier and its parcel id, for the buyer"
    )


class ImportIn(BaseModel):
    csv: str = Field(
        max_length=2_000_000, description="The file's text: a header row, then one per row"
    )
    dry_run: bool = Field(default=False, description="Check the file and show what would be saved")


class ImportOut(BaseModel):
    saved: bool
    count: int
    preview: list[Product]  # the first few, as they'll be saved


@router.get("/api/products")
async def products(store: Shop) -> list[Product]:
    return await store.products()


@router.put("/api/products")
async def replace_products(body: list[Product], store: Shop) -> list[Product]:
    """The products become exactly these, in this order (with their stock). Live from the
    next message."""
    await store.replace_products(body)
    return body


@router.post("/api/products/import")
async def import_products(body: ImportIn, store: Shop) -> ImportOut:
    """Replaces the products with the CSV's rows. Problems come back per row, and nothing is
    saved until the whole file is clean. `dry_run` only checks it."""
    found = products_from_csv(body.csv)
    if not body.dry_run:
        await store.replace_products(found)
    return ImportOut(saved=not body.dry_run, count=len(found), preview=found[:5])


@router.get("/api/shop/orders")
async def orders(
    store: Shop, page: Paged, status: str | None = None, q: str | None = None
) -> list[ShopOrderOut]:
    """Newest first, a page at a time (`limit`, `before`). `q` matches the code or number,
    the name or the phone."""
    found = await store.find(ShopOrder, status, q, limit=page.limit, before=page.before)
    return [ShopOrderOut.model_validate(o) for o in found]


@router.get("/api/shop/orders/{order_id}")
async def order(order_id: str, store: Shop) -> ShopOrderOut:
    return ShopOrderOut.model_validate(await store.get(ShopOrder, order_id))


@router.patch("/api/shop/orders/{order_id}")
async def change_order(order_id: str, body: OrderChange, store: Shop) -> ShopOrderOut:
    """Move it along (`{"status": "confirmed"}` after calling the buyer), and/or say which
    courier has it (`{"status": "shipped", "tracking": "Steadfast 12345678"}`). Cancelling
    puts its items back in stock."""
    found = await store.get(ShopOrder, order_id)
    moved = await store.move_order(order_id, body.status or found.status, body.tracking)
    return ShopOrderOut.model_validate(moved)


# The buyer's payment pages (D47)

PAID = "Payment received. Your order {code} is placed: the shop will call you to confirm it."
NOT_PAID = "The payment didn't go through. Open the payment link again to try once more."


def page(text: str, status_code: int = 200) -> HTMLResponse:
    body = (
        '<!doctype html><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>Payment</title>"
        '<p style="font: 18px system-ui; max-width: 32em; margin: 3em auto; padding: 0 1em">'
        f"{html.escape(text, quote=False)}<br><br>You can close this page and go back to the chat.</p>"
    )
    return HTMLResponse(body, status_code)


def order_page(order: ShopOrder | None) -> HTMLResponse:
    if order is None:
        return page("There's no such order.", 404)
    if order.status == "unpaid":
        return page(NOT_PAID)
    if order.status == "cancelled":
        return page(f"Order {order.code} was cancelled.")
    return page(PAID.replace("{code}", order.code))


@router.get("/pay/{order_id}", include_in_schema=False, response_model=None)
async def pay(order_id: str, services: Svc) -> HTMLResponse | RedirectResponse:
    """The link the agent sends: off to the gateway to pay the delivery charge."""
    order = await services.payments.order(order_id)
    if order is None or order.status != "unpaid":
        return order_page(order)
    try:
        return RedirectResponse(await services.payments.start(order), status_code=303)
    except PaymentUnavailable:
        return page("The payment page isn't answering. Please try the link again in a minute.")


@router.post("/pay/{order_id}/back", include_in_schema=False)
async def paid(order_id: str, services: Svc, val_id: Annotated[str, Form()] = "") -> HTMLResponse:
    """The gateway sends the buyer back here, paid, failed or cancelled. Only `val_id`, checked
    with the gateway, counts; what else it posts could be forged."""
    if val_id:
        with suppress(PaymentUnavailable):  # then the gateway's own notice settles it
            await services.payments.settle(val_id)
    return order_page(await services.payments.order(order_id))


@router.post(NOTIFY_PATH, include_in_schema=False)
async def payment_notice(services: Svc, val_id: Annotated[str, Form()] = "") -> dict[str, bool]:
    """The gateway's notice (SSLCommerz's IPN), even if the buyer closed the tab."""
    if val_id:
        await services.payments.settle(val_id)
    return {"ok": True}
