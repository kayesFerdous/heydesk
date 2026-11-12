"""A shop's own routes: its products, and the orders the agent took; and a product's public
page, its *Buy now* form and an order's page."""

import logging
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, File, Form, Query, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from heydesk.api.csv_export import EXPORT_ROWS, amount_cell, csv_file, phone_cell
from heydesk.api.deps import Paging, Shop, Svc, WorkspaceId
from heydesk.errors import InvalidConfig, NotFound
from heydesk.runtime.commit_check import confirm
from heydesk.runtime.speech import local_time
from heydesk.services.agents import LiveAgent
from heydesk.shop import html_pages
from heydesk.shop.checkout import CartLine, TeamOrder, checked_order
from heydesk.shop.csv_import import PRODUCT_COLUMNS, Suggestion, products_from_csv, suggest_columns
from heydesk.shop.models import ShopOrder
from heydesk.shop.orders import Buyer, BuyerHistory, BuyForm, Coupon, lines_text
from heydesk.shop.products import Product
from heydesk.shop.store import OutOfStock, ShopStore

log = logging.getLogger(__name__)
router = APIRouter(tags=["shop"])


class ImportIn(BaseModel):
    """A CSV file of products."""

    csv: str = Field(
        max_length=2_000_000, description="The file's text: a header row, then one per row"
    )
    dry_run: bool = Field(default=False, description="Check the file and show what would be saved")
    mapping: dict[str, str] | None = Field(
        default=None,
        description="Our field → the file's column, as the owner checked it after /suggest "
        ". Without it, the file's columns are matched to ours by name",
    )


class SuggestIn(BaseModel):
    csv: str = Field(max_length=2_000_000, description="The file's text, as for the import")


class ImportOut[T](BaseModel):
    saved: bool
    count: int
    preview: list[T]  # the first few, as they'll be saved
    added: int | None = None  # a shop's import: how many were new; the rest were updated


class ShopOrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    code: str  # what the buyer was told: "O-101"
    conversation_id: str | None
    source: str  # where it was placed: chat, page or team
    status: str  # new → confirmed → shipped → delivered or returned; or cancelled
    name: str
    phone: str
    lines: list[dict[str, Any]]  # {code, name, size, colour, quantity, price}
    subtotal: int  # minor units
    delivery_charge: int
    discount: int  # a coupon took this off the items
    coupon: str  # its code; "" none
    total: int  # the whole order
    paid: int  # paid online first; the rider collects `total - paid`
    payment_ref: str  # the payment gateway's id for it
    currency: str
    zone: str
    address: str
    note: str
    parcel_id: str  # booked with the courier; "" not booked
    courier_status: str  # the courier's last words about it
    paid_in: bool  # once it ended: the rider's cash reached the shop
    reason: str  # why it came back or was cancelled: "refused", "damaged"
    buyer: BuyerHistory = BuyerHistory()  # this mobile's orders with the shop, this one too
    created_at: datetime
    updated_at: datetime


class OrderChange(BaseModel):
    status: str | None = None  # where it may go depends on where it is: see the 409 message
    paid_in: bool | None = None  # once it's delivered, returned or cancelled
    reason: str | None = Field(default=None, max_length=200)  # the same


@router.get("/api/products")
async def products(store: Shop) -> list[Product]:
    return await store.products()


@router.put("/api/products")
async def replace_products(
    body: list[Product], store: Shop, services: Svc, workspace_id: WorkspaceId
) -> list[Product]:
    """The products become exactly these, in this order (with their stock and photos). Live
    from the next message. While a Google Sheet or WooCommerce store is linked too: a product
    that came from it changes back when it changes there. Only a photo new to the
    list is checked: one saved before stays, from wherever it was kept then."""
    had = {url for p in await store.products() for url in p.photos}
    wrong = {
        f"{n}.photos": f"{url} isn't one of your photos: add it with POST /api/products/photos"
        for n, product in enumerate(body)
        for url in product.photos
        if url not in had and not services.photos.is_product_photo(workspace_id, url)
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
    workspace_id: WorkspaceId,
    _: Shop,
) -> PhotoOut:
    """Adds a product photo: saved as a JPEG and indexed, so a visitor's photo can find it.
    It belongs to a product once that product's `photos` are saved with its URL."""
    photo = await services.photos.add_product_photo(workspace_id, await file.read())
    return PhotoOut(url=photo.url)


@router.post("/api/products/import/suggest")
async def suggest_products_columns(body: SuggestIn, _: Shop) -> Suggestion:
    """The file's columns, a few rows and our fields, with a guess at which column is which
    field. The owner checks it, then imports with that `mapping`."""
    return suggest_columns(body.csv, PRODUCT_COLUMNS)


@router.post("/api/products/import")
async def import_products(body: ImportIn, store: Shop) -> ImportOut[Product]:
    """Adds the CSV's rows to the products: a row with the code, or else the name, of
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
        agent = await services.agents.by_public_key(public_key)
        store = await services.agents.store(agent.workspace_id)
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
    """A product's public page, linked from its card in a chat (`from`: the app it was
    sent on; `c`: the chat, which only the form's post reads). Only that shop's product, as
    HTML; anything else is the same 404."""
    found = await _product(services, public_key, code)
    if found is None:
        return html_page(html_pages.not_found(), 404)
    agent, item = found
    config = agent.config
    back = await services.channels.chat_link(agent.workspace_id, via)
    image = (
        services.channels.absolute_url(item.photos[0]) if item.photos else ""
    )  # a link's preview
    return html_page(
        html_pages.product_page(
            item, config.business_name, config.currency, back, config.delivery, image=image
        )
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
    """*Buy now*. The first post shows the order read back; *Place order* posts it
    again with `heard`, that read-back, and the order is saved only if Python's read-back of
    these details is still the same (`confirm`): the buyer's yes. The owner hears of it as
    of a chat's order; the chat it came from (`c`) gets the receipt."""
    found = await _product(services, public_key, code)
    if found is None:
        return html_page(html_pages.not_found(), 404)
    agent, item = found
    config = agent.config
    back = await services.channels.chat_link(agent.workspace_id, via)
    shop, conversation = await services.orders.page_store(agent, chat)

    def again(problem: str = "") -> HTMLResponse:  # the product page, the form filled in
        return html_page(
            html_pages.product_page(
                item,
                config.business_name,
                config.currency,
                back,
                config.delivery,
                form,
                problem or "Change what you need, then check it again.",
            )
        )

    wanted = CartLine(code=code, quantity=form.quantity, size=form.size, colour=form.colour)
    checked = await checked_order(shop, config, [wanted], form)
    if form.change or isinstance(checked, str):
        return again("" if form.change else str(checked))
    order, read_back, how = checked
    confirmed = confirm(form.heard, order, read_back)
    if confirmed is None:
        zone = config.zone(order.zone)
        days = zone.days if zone else ""
        return html_page(
            html_pages.check_page(
                item, config.business_name, order, how, read_back, form, days, bool(form.heard)
            )
        )
    try:
        saved = await services.orders.place_from_page(shop, conversation, confirmed, read_back, how)
    except OutOfStock:
        return again("Sold out since you checked. Choose another size or colour.")
    return RedirectResponse(f"/o/{saved.id}", status_code=303)  # on this same server


@router.get("/o/{order_id}", response_model=None, include_in_schema=False)
async def order_status(order_id: str, services: Svc) -> HTMLResponse:
    """An order's page, linked from its receipt: the id is long and random, as the
    payment link's is, so no one guesses another."""
    found = await services.payments.find(order_id)
    if not isinstance(found, ShopOrder):
        return html_page(html_pages.not_found(), 404)
    agent = await services.agents.by_workspace(found.workspace_id)
    pay = f"/pay/{found.id}"
    return html_page(html_pages.order_page(found, agent.config.business_name, pay))


def html_page(text: str, status_code: int = 200) -> HTMLResponse:
    headers = {"Content-Security-Policy": html_pages.POLICY, "Referrer-Policy": "no-referrer"}
    return HTMLResponse(text, status_code, headers=headers)


@router.get("/api/shop/orders")
async def orders(
    store: Shop, page: Paging, status: str | None = None, q: str | None = None
) -> list[ShopOrderOut]:
    """Newest first, a page at a time (`limit`, `before`). `q` matches the code or number,
    the name or the phone."""
    found = await store.find(ShopOrder, status, q, limit=page.limit, before=page.before)
    return await with_buyers(store, found)


@router.get("/api/shop/orders/{order_id}")
async def order(order_id: str, store: Shop) -> ShopOrderOut:
    return await with_buyer(store, await store.get(ShopOrder, order_id))


@router.patch("/api/shop/orders/{order_id}")
async def change_order(
    order_id: str, body: OrderChange, store: Shop, services: Svc
) -> ShopOrderOut:
    """Move it along (`{"status": "confirmed"}` after calling the buyer, `packed`, `on_hold`,
    `shipped` once the owner has booked a courier by hand; in development the fake courier books it).
    Cancelling puts its items back in stock. The buyer hears it in their chat when it's
    confirmed, shipped or cancelled, as the team's message. Once it ended, `paid_in` and
    `reason` too, in the same request or later."""
    moved = await services.orders.move(store, order_id, body.status, body.paid_in, body.reason)
    return await with_buyer(store, moved)


@router.post("/api/shop/orders", status_code=201)
async def add_order(body: TeamOrder, store: Shop, services: Svc) -> ShopOrderOut:
    """An order the team types for a buyer who called: checked and added up as the
    chat's are, then saved `confirmed` with no yes asked, since the team member is the yes.
    The owner hears of it as of any new order."""
    return await with_buyer(store, await services.orders.add_team_order(store, body))


async def with_buyer(store: ShopStore, order: ShopOrder) -> ShopOrderOut:
    """One order with its buyer's record."""
    [out] = await with_buyers(store, [order])
    return out


async def with_buyers(store: ShopStore, orders: list[ShopOrder]) -> list[ShopOrderOut]:
    """Each order with its buyer's record, counted for all of them at once."""
    records = await store.buyer_histories({o.phone for o in orders})
    return [
        ShopOrderOut.model_validate(o).model_copy(update={"buyer": records[o.phone]})
        for o in orders
    ]


@router.get("/api/shop/coupons")
async def coupons(store: Shop) -> list[Coupon]:
    """The shop's coupon codes, with how many orders used each."""
    return await store.coupons()


@router.post("/api/shop/coupons", status_code=201)
async def add_coupon(body: Coupon, store: Shop) -> Coupon:
    """A new code, live from the next message: the buyer says it, `update_cart` checks it."""
    return await store.add_coupon(body)


@router.delete("/api/shop/coupons/{code}", status_code=204)
async def delete_coupon(code: str, store: Shop) -> None:
    """The code stops working; orders placed with it keep their discount."""
    await store.delete_coupon(code)


class OfferIn(BaseModel):
    """An offer to everyone who wrote in the last `within_hours`."""

    text: str = Field(min_length=1, max_length=500)  # sent as it is: "Free delivery till 10 pm!"
    kind: Literal["percent", "amount", "free_delivery"]
    value: int = Field(default=0, ge=0)  # as a coupon's
    ends_at: AwareDatetime
    within_hours: int = Field(default=24, ge=1, le=168)  # who it reaches: up to a week back


class OfferOut(BaseModel):
    code: str  # the coupon it rides on: "OFFER1"
    sent_to: int  # how many chats got it


@router.post("/api/shop/offers", status_code=201)
async def send_offer(body: OfferIn, store: Shop, services: Svc) -> OfferOut:
    """Sends the offer to each chat where the buyer wrote in the last `within_hours`, inside
    the app's window (`tell_visitor_if_allowed`: Messenger and WhatsApp only within 24 hours), and saves
    it as a coupon only those chats may use, until `ends_at`; `update_cart` takes it off by
    itself."""
    code, sent_to = await services.orders.send_offer(
        store, body.text, body.kind, body.value, body.ends_at, body.within_hours
    )
    return OfferOut(code=code, sent_to=sent_to)


@router.get("/api/shop/buyers")
async def buyers(store: Shop, page: Paging, q: str | None = None) -> list[Buyer]:
    """The shop's buyers by mobile, the latest to order first, with how their orders ended,
    what they spent and where they wrote from. `q`: a mobile or a name."""
    return await store.buyers(q, limit=page.limit)


@router.get("/api/shop/orders.csv", response_model=None)
async def orders_csv(
    store: Shop,
    services: Svc,
    workspace_id: WorkspaceId,
    status: str | None = None,
    q: str | None = None,
) -> Response:
    """The orders the list shows with this `status` and `q`, as a spreadsheet's file:
    money in taka, times in the business's own time."""
    tz = (await services.agents.by_workspace(workspace_id)).config.timezone
    found = await store.find(ShopOrder, status, q, limit=EXPORT_ROWS)
    rows = [
        [
            o.code,
            local_time(o.created_at, tz),
            o.status,
            o.name,
            phone_cell(o.phone),
            o.address,
            o.zone,
            lines_text(o.lines),
            amount_cell(o.subtotal),
            amount_cell(o.delivery_charge),
            amount_cell(o.total),
            amount_cell(o.paid),
            amount_cell(o.total - o.paid),
            "yes" if o.paid_in else "",
            o.reason,
            o.parcel_id,
            o.note,
        ]
        for o in found
    ]
    return csv_file("orders", ORDER_COLUMNS, rows)


ORDER_COLUMNS = [
    "Order",
    "Date",
    "Status",
    "Name",
    "Mobile",
    "Address",
    "Area",
    "Items",
    "Subtotal",
    "Delivery",
    "Total",
    "Paid online",
    "To collect",
    "Money in",
    "Reason",
    "Parcel",
    "Note",
]
