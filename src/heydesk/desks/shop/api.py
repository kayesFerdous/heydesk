"""A shop's own routes: its products, and the orders the agent took."""

from datetime import datetime
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, File, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from heydesk.api.deps import ImportIn, ImportOut, Paged, SuggestIn, Svc, Workspace
from heydesk.desks.csv_import import Suggestion
from heydesk.desks.shop.models import ShopOrder
from heydesk.desks.shop.store import ShopStore
from heydesk.desks.shop.values import PRODUCT_COLUMNS, Product, products_from_csv
from heydesk.errors import InvalidConfig

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
    parcel_id: str  # booked with the linked courier (D90); empty: not booked by us
    courier_status: str  # the courier's last words about it
    courier_record: str  # the mobile across the courier's merchants (D91); "" not known
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
async def replace_products(
    body: list[Product], store: Shop, services: Svc, ws: Workspace
) -> list[Product]:
    """The products become exactly these, in this order (with their stock and photos). Live
    from the next message. While a Google Sheet is the list, only their photos are taken (D58)."""
    wrong = {
        f"{n}.photos": f"{url} isn't one of your photos: add it with POST /api/products/photos"
        for n, product in enumerate(body)
        for url in product.photos
        if not services.photos.is_products(ws, url)
    }
    if wrong:
        raise InvalidConfig(wrong)
    if await store.linked():  # the rest is the sheet's
        photos = {p.code: p.photos for p in body}
        body = [
            p.model_copy(update={"photos": photos.get(p.code, p.photos)})
            for p in await store.products()
        ]
    await store.replace_products(body)
    return body


class PhotoOut(BaseModel):
    url: str  # put it in a product's `photos` and save the products


@router.post("/api/products/photos")
async def add_product_photo(
    file: Annotated[UploadFile, File(description="A JPEG, PNG or WebP photo, up to 10 MB")],
    services: Svc,
    ws: Workspace,
    _: Shop,
) -> PhotoOut:
    """Adds a product photo: saved as a JPEG and indexed, so a visitor's photo can find it
    (D57). It belongs to a product once that product's `photos` are saved with its URL."""
    photo = await services.photos.add_product_photo(ws, await file.read())
    return PhotoOut(url=photo.url)


@router.post("/api/products/import/suggest")
async def suggest_products_columns(body: SuggestIn, services: Svc, _: Shop) -> Suggestion:
    """The file's columns, a few rows and our fields, with the AI's guess at which column is
    which field. The owner checks it, then imports with that `mapping` (D53)."""
    return await services.imports.suggest(body.csv, PRODUCT_COLUMNS)


@router.post("/api/products/import")
async def import_products(body: ImportIn, store: Shop) -> ImportOut[Product]:
    """Replaces the products with the CSV's rows. Problems come back per row, and nothing is
    saved until the whole file is clean. `dry_run` only checks it."""
    await store.not_linked()
    found = products_from_csv(body.csv, body.mapping)
    photos = {p.code: p.photos for p in await store.products()}  # a CSV has no photos: keep them
    found = [p.model_copy(update={"photos": photos.get(p.code, [])}) for p in found]
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
async def change_order(
    order_id: str, body: OrderChange, store: Shop, services: Svc, ws: Workspace
) -> ShopOrderOut:
    """Move it along (`{"status": "confirmed"}` after calling the buyer), and/or say which
    courier has it (`{"status": "shipped", "tracking": "Steadfast 12345678"}`). Cancelling
    puts its items back in stock. With a courier linked, a confirmed order is booked there
    at once (D90)."""
    found = await store.get(ShopOrder, order_id)
    moved = await store.move_order(order_id, body.status or found.status, body.tracking)
    if moved.status == "confirmed":
        services.courier.book_soon(ws)
    return ShopOrderOut.model_validate(moved)
