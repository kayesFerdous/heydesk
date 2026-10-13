"""A shop's own routes: its products, and the orders the agent took."""

from datetime import datetime
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from heydesk.api.deps import ImportIn, ImportOut, Paged, SuggestIn, Svc, Workspace
from heydesk.desks.csv_import import Suggestion
from heydesk.desks.shop.models import ShopOrder
from heydesk.desks.shop.store import ShopStore
from heydesk.desks.shop.values import PRODUCT_COLUMNS, Product, products_from_csv

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


@router.get("/api/products")
async def products(store: Shop) -> list[Product]:
    return await store.products()


@router.put("/api/products")
async def replace_products(body: list[Product], store: Shop) -> list[Product]:
    """The products become exactly these, in this order (with their stock). Live from the
    next message."""
    await store.replace_products(body)
    return body


@router.post("/api/products/import/suggest")
async def suggest_products_columns(body: SuggestIn, services: Svc, _: Shop) -> Suggestion:
    """The file's columns, a few rows and our fields, with the AI's guess at which column is
    which field. The owner checks it, then imports with that `mapping` (D53)."""
    return await services.imports.suggest(body.csv, PRODUCT_COLUMNS)


@router.post("/api/products/import")
async def import_products(body: ImportIn, store: Shop) -> ImportOut[Product]:
    """Replaces the products with the CSV's rows. Problems come back per row, and nothing is
    saved until the whole file is clean. `dry_run` only checks it."""
    found = products_from_csv(body.csv, body.mapping)
    if not body.dry_run:
        await store.replace_products(found)
    return ImportOut[Product](saved=not body.dry_run, count=len(found), preview=found[:5])


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
