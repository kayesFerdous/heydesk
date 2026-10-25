"""A shop's own routes: its products, and the orders the agent took; and a product's public
page (D121)."""

from datetime import datetime
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict

from heydesk.api.deps import ImportIn, ImportOut, Paged, SuggestIn, Svc, Workspace
from heydesk.desks.csv_import import Suggestion, suggest
from heydesk.desks.shop import page
from heydesk.desks.shop.models import ShopOrder
from heydesk.desks.shop.store import ShopStore
from heydesk.desks.shop.values import PRODUCT_COLUMNS, Product, products_from_csv
from heydesk.errors import InvalidConfig, NotFound

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
    parcel_id: str  # booked with the courier (D112); "" not booked
    courier_status: str  # the courier's last words about it
    created_at: datetime
    updated_at: datetime


class OrderChange(BaseModel):
    status: str | None = None  # where it may go depends on where it is: see the 409 message


@router.get("/api/products")
async def products(store: Shop) -> list[Product]:
    return await store.products()


@router.put("/api/products")
async def replace_products(
    body: list[Product], store: Shop, services: Svc, ws: Workspace
) -> list[Product]:
    """The products become exactly these, in this order (with their stock and photos). Live
    from the next message. While a Google Sheet or WooCommerce store is linked too: a product
    that came from it changes back when it changes there (D120). Only a photo new to the
    list is checked: one saved before stays, from wherever it was kept then (D101)."""
    had = {url for p in await store.products() for url in p.photos}
    wrong = {
        f"{n}.photos": f"{url} isn't one of your photos: add it with POST /api/products/photos"
        for n, product in enumerate(body)
        for url in product.photos
        if url not in had and not services.photos.is_products(ws, url)
    }
    if wrong:
        raise InvalidConfig(wrong)
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
async def suggest_products_columns(body: SuggestIn, _: Shop) -> Suggestion:
    """The file's columns, a few rows and our fields, with a guess at which column is which
    field (D115). The owner checks it, then imports with that `mapping` (D53)."""
    return suggest(body.csv, PRODUCT_COLUMNS)


@router.post("/api/products/import")
async def import_products(body: ImportIn, store: Shop) -> ImportOut[Product]:
    """Adds the CSV's rows to the products (D120): a row with the code, or else the name, of
    one we have updates it, and the rest are added; none is deleted. Problems come back per
    row, and nothing is saved until the whole file is clean. `dry_run` only checks it."""
    found = products_from_csv(body.csv, body.mapping)  # a dry run shows the photo links
    new = None if body.dry_run else await store.add_products(found)
    return ImportOut[Product](
        saved=not body.dry_run, count=len(found), preview=found[:5], added=new
    )


@router.get("/p/{public_key}/{code}", response_model=None, include_in_schema=False)
async def product(
    public_key: str,
    code: str,
    services: Svc,
    via: Annotated[str, Query(alias="from")] = "web",
) -> HTMLResponse:
    """A product's public page (D121), linked from its card in a chat (`from`: the app it was
    sent on). Only that shop's product, as HTML; anything else is the same 404."""
    try:
        agent = await services.agents.by_key(public_key)
        store = cast(ShopStore, await services.agents.store(agent.workspace_id, "shop"))
    except NotFound:
        return html_page(page.not_found(), 404)
    found = next((p for p in await store.products() if p.code == code), None)
    if found is None:
        return html_page(page.not_found(), 404)
    back = await services.channels.chat_link(agent.workspace_id, via)
    config = agent.config
    return html_page(page.product_page(found, config.business_name, config.currency, back))


def html_page(text: str, status_code: int = 200) -> HTMLResponse:
    headers = {"Content-Security-Policy": page.POLICY, "Referrer-Policy": "no-referrer"}
    return HTMLResponse(text, status_code, headers=headers)


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
    order_id: str, body: OrderChange, store: Shop, services: Svc
) -> ShopOrderOut:
    """Move it along (`{"status": "confirmed"}` after calling the buyer, `shipped` once the
    owner has booked a courier by hand; D112 books it in development). Cancelling puts its items back in stock. The buyer hears
    it in their chat when it's confirmed, shipped or cancelled, as the team's message (D66)."""
    found = await store.get(ShopOrder, order_id)
    moved = await store.move_order(order_id, body.status or found.status)
    if moved.status != found.status and moved.conversation_id and (line := moved.news()):
        await services.team.tell_visitor(moved.conversation_id, line)
    return ShopOrderOut.model_validate(moved)
