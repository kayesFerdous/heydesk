"""A shop's own routes: its products, and the orders the agent took; and a product's public
page, its *Buy now* form and an order's page."""

import logging
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from heydesk.api.deps import (
    EXPORT_ROWS,
    ImportIn,
    ImportOut,
    Paged,
    SuggestIn,
    Svc,
    Workspace,
    amount_cell,
    csv_file,
    phone_cell,
)
from heydesk.errors import Conflict, InvalidConfig, NotFound
from heydesk.runtime import cards
from heydesk.runtime.events import Card
from heydesk.runtime.guards import confirm
from heydesk.runtime.speech import local_time
from heydesk.services.agents import LiveAgent
from heydesk.services.channels import card_text
from heydesk.shop import page
from heydesk.shop.csv_import import PRODUCT_COLUMNS, Suggestion, products_from_csv, suggest
from heydesk.shop.models import ShopOrder
from heydesk.shop.orders import STATUS_WORDS, Buyer, BuyerRecord, BuyForm, Coupon, lines_text
from heydesk.shop.products import Product
from heydesk.shop.store import OutOfStock, ShopStore
from heydesk.shop.tools.cart import CartLine
from heydesk.shop.tools.orders import TeamOrder, checked_order, order_alert

log = logging.getLogger(__name__)
router = APIRouter(tags=["shop"])


async def shop_store(services: Svc, workspace_id: Workspace) -> ShopStore:
    """404 until the business has an agent."""
    return await services.agents.store(workspace_id)


type Shop = Annotated[ShopStore, Depends(shop_store)]


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
    buyer: BuyerRecord = BuyerRecord()  # this mobile's orders with the shop, this one too
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
    body: list[Product], store: Shop, services: Svc, workspace_id: Workspace
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
    workspace_id: Workspace,
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
    return suggest(body.csv, PRODUCT_COLUMNS)


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
        agent = await services.agents.by_key(public_key)
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
        return html_page(page.not_found(), 404)
    agent, item = found
    config = agent.config
    back = await services.channels.chat_link(agent.workspace_id, via)
    image = (
        services.channels.absolute_url(item.photos[0]) if item.photos else ""
    )  # a link's preview
    return html_page(
        page.product_page(
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
        return html_page(page.not_found(), 404)
    agent, item = found
    config = agent.config
    back = await services.channels.chat_link(agent.workspace_id, via)
    shop, conversation = await services.conversations.store_for(agent, chat)

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

    wanted = CartLine(code=code, quantity=form.quantity, size=form.size, colour=form.colour)
    checked = await checked_order(shop, config, [wanted], form)
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
        saved = await shop.place_order(confirmed, "page")
    except OutOfStock:
        return again("Sold out since you checked. Choose another size or colour.")
    workspace_id = agent.workspace_id
    if conversation:
        receipt = cards.receipt(
            "Order placed",
            saved.code,
            STATUS_WORDS[saved.status].capitalize(),
            [cards.row("Items", lines_text(order.lines)), cards.row("Payment", how)],
            cards.amount(saved.total, saved.currency),
            order_url=shop.order_link(saved.id),
        )
        said = card_text(Card(receipt["kind"], receipt["data"]), "web")  # plain words
        await services.team.tell_visitor(conversation.id, said)
    if saved.status != "unpaid":  # the owner hears of an unpaid one once it's paid
        alert = await order_alert(shop, saved.code, read_back, order.phone, order.lines)
        await (
            services.team.alert(conversation, alert)
            if conversation
            else services.team.notify(workspace_id, alert)
        )
        services.woo.push_soon(workspace_id)  # into a linked WooCommerce store now
    log.info("page order", extra={"workspace_id": workspace_id, "order_id": saved.id})
    return RedirectResponse(f"/o/{saved.id}", status_code=303)  # on this same server


@router.get("/o/{order_id}", response_model=None, include_in_schema=False)
async def order_status(order_id: str, services: Svc) -> HTMLResponse:
    """An order's page, linked from its receipt: the id is long and random, as the
    payment link's is, so no one guesses another."""
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
    return await with_buyers(store, found)


@router.get("/api/shop/orders/{order_id}")
async def order(order_id: str, store: Shop) -> ShopOrderOut:
    [found] = await with_buyers(store, [await store.get(ShopOrder, order_id)])
    return found


@router.patch("/api/shop/orders/{order_id}")
async def change_order(
    order_id: str, body: OrderChange, store: Shop, services: Svc
) -> ShopOrderOut:
    """Move it along (`{"status": "confirmed"}` after calling the buyer, `packed`, `on_hold`,
    `shipped` once the owner has booked a courier by hand; in development the fake courier books it).
    Cancelling puts its items back in stock. The buyer hears it in their chat when it's
    confirmed, shipped or cancelled, as the team's message. Once it ended, `paid_in` and
    `reason` too, in the same request or later."""
    found = await store.get(ShopOrder, order_id)
    moved = await store.move_order(order_id, body.status or found.status)
    if (
        moved.status != found.status
        and moved.conversation_id
        and (line := moved.status_update_text())
    ):
        await services.team.tell_visitor(moved.conversation_id, line)
    if body.paid_in is not None or body.reason is not None:
        moved = await store.record_outcome(order_id, body.paid_in, body.reason)
    [out] = await with_buyers(store, [moved])
    return out


@router.post("/api/shop/orders", status_code=201)
async def add_order(
    body: TeamOrder, store: Shop, services: Svc, workspace_id: Workspace
) -> ShopOrderOut:
    """An order the team types for a buyer who called: checked and added up as the
    chat's are, then saved `confirmed` with no yes asked, since the team member is the yes.
    The owner hears of it as of any new order."""
    config = (await services.agents.by_workspace(workspace_id)).config
    checked = await checked_order(store, config, body.lines, body)
    if isinstance(checked, str):
        raise InvalidConfig({"order": checked})
    order, read_back, _ = checked
    try:
        saved = await store.add_team_order(order)
    except OutOfStock as error:
        raise Conflict(f"Not enough in stock: {error}.") from None
    alert = await order_alert(store, saved.code, read_back, saved.phone, saved.lines)
    await services.team.notify(
        workspace_id, alert.replace("New order", "New order, typed by the team", 1)
    )
    services.woo.push_soon(workspace_id)
    [out] = await with_buyers(store, [saved])
    return out


async def with_buyers(store: ShopStore, orders: list[ShopOrder]) -> list[ShopOrderOut]:
    """Each order with its buyer's record, counted for all of them at once."""
    records = await store.buyer_records({o.phone for o in orders})
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
    now = store.now()
    if body.ends_at <= now:
        raise InvalidConfig({"ends_at": "Pick an end time that's still to come."})
    code = f"OFFER{await store.offers_made() + 1}"
    try:
        coupon = Coupon(code=code, kind=body.kind, value=body.value, ends_at=body.ends_at)
    except ValueError as error:
        raise InvalidConfig({"value": str(error)}) from None
    text = " ".join(body.text.split())
    sent = [
        chat
        for chat in await store.chats_since(now - timedelta(hours=body.within_hours))
        if await services.team.tell_visitor_if_allowed(chat, text)
    ]
    await store.add_offer(coupon, sent)
    log.info("offer sent", extra={"workspace_id": store.workspace_id, "chats": len(sent)})
    return OfferOut(code=code, sent_to=len(sent))


@router.get("/api/shop/buyers")
async def buyers(store: Shop, page: Paged, q: str | None = None) -> list[Buyer]:
    """The shop's buyers by mobile, the latest to order first, with how their orders ended,
    what they spent and where they wrote from. `q`: a mobile or a name."""
    return await store.buyers(q, limit=page.limit)


@router.get("/api/shop/orders.csv", response_model=None)
async def orders_csv(
    store: Shop,
    services: Svc,
    workspace_id: Workspace,
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
