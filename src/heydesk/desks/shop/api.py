"""A shop's own routes: its products, and the orders the agent took; and a product's public
page (D121), its *Buy now* form and an order's page (D130)."""

import logging
from datetime import datetime
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict

from heydesk.api.deps import ImportIn, ImportOut, Paged, SuggestIn, Svc, Workspace
from heydesk.desks.csv_import import Suggestion, suggest
from heydesk.desks.shop import page
from heydesk.desks.shop.models import ShopOrder
from heydesk.desks.shop.store import OutOfStock, ShopStore
from heydesk.desks.shop.tools import form_order, order_alert
from heydesk.desks.shop.values import (
    PRODUCT_COLUMNS,
    STATUS_WORDS,
    BuyForm,
    Product,
    ShopConfig,
    lines_text,
    products_from_csv,
)
from heydesk.errors import InvalidConfig, NotFound
from heydesk.runtime import cards
from heydesk.runtime.events import Card
from heydesk.runtime.guards import confirm
from heydesk.services.agents import LiveAgent
from heydesk.services.channels import card_text

log = logging.getLogger(__name__)
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


async def _product(services: Svc, public_key: str, code: str) -> tuple[LiveAgent, Product] | None:
    """The shop's agent and the product; None for a wrong key or code."""
    try:
        agent = await services.agents.by_key(public_key)
        store = cast(ShopStore, await services.agents.store(agent.workspace_id, "shop"))
    except NotFound:
        return None
    found = next((p for p in await store.products() if p.code == code), None)
    return (agent, found) if found else None


@router.get("/p/{public_key}/{code}", response_model=None, include_in_schema=False)
async def product(
    public_key: str,
    code: str,
    services: Svc,
    via: Annotated[str, Query(alias="from")] = "web",
) -> HTMLResponse:
    """A product's public page (D121), linked from its card in a chat (`from`: the app it was
    sent on; `c`: the chat, which only the form's post reads). Only that shop's product, as
    HTML; anything else is the same 404."""
    found = await _product(services, public_key, code)
    if found is None:
        return html_page(page.not_found(), 404)
    agent, item = found
    config = cast(ShopConfig, agent.config)
    back = await services.channels.chat_link(agent.workspace_id, via)
    return html_page(
        page.product_page(item, config.business_name, config.currency, back, config.delivery)
    )


@router.post("/p/{public_key}/{code}", response_model=None, include_in_schema=False)
async def buy(
    public_key: str,
    code: str,
    form: Annotated[BuyForm, Form()],
    services: Svc,
    via: Annotated[str, Query(alias="from")] = "web",
    chat: Annotated[str, Query(alias="c")] = "",
) -> HTMLResponse | RedirectResponse:
    """*Buy now* (D130). The first post shows the order read back; *Place order* posts it
    again with `heard`, that read-back, and the order is saved only if Python's read-back of
    these details is still the same (`confirm`): the buyer's yes. The owner hears of it as
    of a chat's order; the chat it came from (`c`) gets the receipt."""
    found = await _product(services, public_key, code)
    if found is None:
        return html_page(page.not_found(), 404)
    agent, item = found
    config = cast(ShopConfig, agent.config)
    back = await services.channels.chat_link(agent.workspace_id, via)
    store, conv = await services.conversations.store_for(agent, chat)
    shop = cast(ShopStore, store)

    def again(problem: str = "") -> HTMLResponse:  # the product page, the form filled in
        return html_page(
            page.product_page(
                item,
                config.business_name,
                config.currency,
                back,
                config.delivery,
                form,
                problem or "Change what you need, then check it again.",
            )
        )

    checked = await form_order(shop, config, code, form)
    if form.change or isinstance(checked, str):
        return again("" if form.change else str(checked))
    order, read_back, how = checked
    confirmed = confirm(form.heard, order, read_back)
    if confirmed is None:
        days = next((z.days for z in config.delivery if z.name == order.zone), "")
        return html_page(
            page.check_page(
                item, config.business_name, order, how, read_back, form, days, bool(form.heard)
            )
        )
    try:
        saved = await shop.place_order(confirmed)
    except OutOfStock:
        return again("Sold out since you checked. Choose another size or colour.")
    ws = agent.workspace_id
    if conv:
        receipt = cards.receipt(
            "Order placed",
            saved.code,
            STATUS_WORDS[saved.status].capitalize(),
            [cards.row("Items", lines_text(order.lines)), cards.row("Payment", how)],
            cards.amount(saved.total, saved.currency),
            order_url=shop.order_link(saved.id),
        )
        said = card_text(Card(receipt["kind"], receipt["data"]), "web")  # plain words
        await services.team.tell_visitor(conv.id, said)
    if saved.status != "unpaid":  # the owner hears of an unpaid one once it's paid (D47)
        alert = await order_alert(shop, saved.code, read_back, order.phone, order.lines)
        await (services.team.alert(conv, alert) if conv else services.team.notify(ws, alert))
        services.woo.push_soon(ws)  # into a linked WooCommerce store now (D77)
    log.info("page order", extra={"workspace_id": ws, "order_id": saved.id})
    return RedirectResponse(f"/o/{saved.id}", status_code=303)  # on this same server


@router.get("/o/{order_id}", response_model=None, include_in_schema=False)
async def order_status(order_id: str, services: Svc) -> HTMLResponse:
    """An order's page, linked from its receipt (D130): the id is long and random, as the
    payment link's is (D47), so no one guesses another."""
    found = await services.payments.find(order_id)
    if not isinstance(found, ShopOrder):
        return html_page(page.not_found(), 404)
    agent = await services.agents.by_workspace(found.workspace_id)
    pay = f"/pay/{found.id}"
    return html_page(page.order_page(found, agent.config.business_name, pay))


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
