"""A product's public page (D121): `/p/<the agent's public key>/<code>`, opened from a product
card in any chat. Server-rendered HTML with only what a buyer may see: photos, name, price,
the sizes and colours in stock, the description. No script runs on it.

Its *Buy now* form (D130) posts back to it: first a page to check the order, read back as the
chat reads it, then *Place order*, the buyer's yes. The order's own page is `/o/<id>`."""

import html

from heydesk.desks.shop.districts import DISTRICTS
from heydesk.desks.shop.models import ShopOrder
from heydesk.desks.shop.values import (
    STATUS_WORDS,
    STEPS,
    BuyForm,
    NewOrder,
    Product,
    Zone,
    lines_text,
)
from heydesk.runtime.speech import money

HEAD = (
    '<!doctype html><html lang="en"><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width, initial-scale=1">'
    '<meta name="robots" content="noindex">'
    "<title>[title]</title>"
    "<style>"
    "body{font:17px/1.5 system-ui,sans-serif;margin:0;background:#f4f3f0;color:#111}"
    "main{max-width:32em;margin:0 auto;padding:1em;background:#fff;min-height:100vh}"
    ".shop{color:#666;font-size:.9em;margin:0 0 .8em}"
    ".photos{display:flex;gap:.5em;overflow-x:auto;scroll-snap-type:x mandatory}"
    ".photos img{width:100%;flex:none;aspect-ratio:1;object-fit:cover;border-radius:16px;"
    "scroll-snap-align:start;background:#eee}"
    "h1{font-size:1.4em;margin:.6em 0 .2em}"
    ".price{font-size:1.3em;font-weight:600}.was{color:#888;text-decoration:line-through;"
    "font-weight:400;margin-left:.4em}"
    ".chips span{display:inline-block;border:1px solid #ccc;border-radius:99px;"
    "padding:.1em .8em;margin:0 .3em .3em 0}"
    ".out{color:#b00020;font-weight:600}"
    ".order{margin-top:1.5em;padding:1em;border-radius:16px;background:#f4f3f0}"
    ".btn{display:block;margin-top:.8em;padding:.8em;border-radius:99px;background:#111;"
    "color:#fff;text-align:center;text-decoration:none;font-weight:600;border:0;width:100%;"
    "font:inherit;font-weight:600;cursor:pointer;box-sizing:border-box}"
    ".light{background:#fff;color:#111;border:1px solid #ccc}"
    "summary.btn{list-style:none}summary.btn::-webkit-details-marker{display:none}"
    "label{display:block;margin-top:.8em;font-weight:600}"
    "input,select,textarea{display:block;width:100%;box-sizing:border-box;margin-top:.3em;"
    "padding:.6em;font:inherit;border:1px solid #ccc;border-radius:10px;background:#fff}"
    ".problem{color:#b00020;font-weight:600}.dim{color:#666;font-size:.9em}"
    "dl{display:grid;grid-template-columns:auto 1fr;gap:.3em 1em}dt{color:#666}dd{margin:0}"
    ".total{font-weight:700}"
    "</style>"
)
# A browser shows the page and its photos, and nothing else loads or runs
POLICY = (
    "default-src 'none'; img-src https: http: data:; style-src 'unsafe-inline'; "
    "form-action 'self'"  # the Buy now form posts only back to us (D130)
)


def product_page(
    product: Product,
    shop: str,
    currency: str,
    back: str,
    zones: list[Zone] | None = None,
    form: BuyForm | None = None,
    problem: str = "",
) -> str:
    """`back` is the chat's own link (Messenger, Telegram), or "" where closing the page goes
    back to it (the website chat, WhatsApp). `zones`: where the shop delivers, for the *Buy
    now* form; `form` and `problem`, the buyer's details again with what to fix."""
    e = html.escape
    photos = "".join(f'<img src="{e(url)}" alt="{e(product.name)}">' for url in product.photos)
    price = e(money(product.price, currency))
    if product.was_price and product.was_price > product.price:
        price += f'<span class="was">{e(money(product.was_price, currency))}</span>'
    body = f'<p class="shop">{e(shop)}</p>'
    if photos:
        body += f'<div class="photos">{photos}</div>'
    body += f'<h1>{e(product.name)}</h1><p class="price">{price}</p>'
    if product.sold_out():
        body += '<p class="out">Sold out</p>'
    else:
        for what, label in (("size", "Sizes"), ("colour", "Colours")):
            left = product.in_stock(what)
            if left:
                chips = "".join(f"<span>{e(choice)}</span>" for choice in left)
                body += f'<p>{label} in stock:</p><p class="chips">{chips}</p>'
    if product.description:
        body += f"<p>{e(product.description)}</p>"
    for n, link in enumerate(product.videos, 1):  # links out, so nothing loads here (D125)
        name = "Watch the video" if len(product.videos) == 1 else f"Watch video {n}"
        body += f'<p><a href="{e(link)}" target="_blank" rel="noopener">▶ {name}</a></p>'
    if zones and not product.sold_out():
        body += buy_form(product, zones, currency, form or BuyForm(), problem)
    order = f"Order [{product.code}]"
    body += f'<div class="order">Or go back to the chat and say <b>{e(order)}</b>.'
    if back:
        body += f'<a class="btn" href="{e(back)}">Back to the chat</a>'
    body += "</div>"
    return page(product.name, body)


def buy_form(
    product: Product, zones: list[Zone], currency: str, form: BuyForm, problem: str
) -> str:
    """*Buy now*: a form that opens in place (no script), posted back to this page."""
    e = html.escape

    def choose(what: str, label: str, chosen: str) -> str:
        left = product.in_stock(what)
        if not left:
            return ""
        options = "".join(
            f"<option{' selected' if c == chosen else ''}>{e(c)}</option>" for c in left
        )
        return f'<label>{label}<select name="{what}" required>{options}</select></label>'

    def field(name: str, label: str, value: str, kind: str = "text", more: str = "") -> str:
        return (
            f'<label>{label}<input name="{name}" type="{kind}" value="{e(value)}" {more}></label>'
        )

    districts = "".join(
        f"<option{' selected' if d == (form.district or 'Dhaka') else ''}>{e(d)}</option>"
        for d in DISTRICTS
    )
    takes = "".join(
        f"<li>{e(z.name)}: {e(money(z.charge, currency))}" + (f", {e(z.days)}" if z.days else "")
        for z in zones
    )
    shown = f'<p class="problem">{e(problem)}</p>' if problem else ""
    return (
        f'<details class="order"{" open" if problem else ""}>'
        '<summary class="btn">Buy now</summary><form method="post">'
        + shown
        + choose("size", "Size", form.size)
        + choose("colour", "Colour", form.colour)
        + field("quantity", "How many", str(form.quantity), "number", 'min="1" max="20" required')
        + field("name", "Your name", form.name, more='required maxlength="100"')
        + field("phone", "Mobile", form.phone, "tel", 'required placeholder="01712345678"')
        + f'<label>Address<textarea name="address" required maxlength="300" rows="3" '
        f'placeholder="House, road, area">{e(form.address)}</textarea></label>'
        + f'<label>District<select name="district" required>{districts}</select></label>'
        + field("note", "Note (if any)", form.note, more='maxlength="500"')
        + f'<p class="dim">Delivery:</p><ul class="dim">{takes}</ul>'
        + '<button class="btn">Check my order</button></form></details>'
    )


def check_page(
    product: Product,
    shop: str,
    order: NewOrder,
    how: str,
    read_back: str,
    form: BuyForm,
    days: str,
    changed: bool,
) -> str:
    """The order read back, as the chat would; *Place order* is the yes (D130). The form's
    details go back with it, and `heard`, what this page said, for Python to compare."""
    e = html.escape
    cur = order.currency
    rows = (
        [
            ("Items", lines_text(order.lines)),
            (
                "Delivery",
                f"{order.zone}: "
                + (money(order.delivery_charge, cur) if order.delivery_charge else "free"),
            ),
            ("Payment", how),
            ("Address", order.address),
            ("Name", order.name),
            ("Mobile", order.phone),
        ]
        + ([("Note", order.note)] if order.note else [])
        + ([("Takes", days)] if days else [])
    )
    listed = "".join(f"<dt>{e(k)}</dt><dd>{e(v)}</dd>" for k, v in rows)
    total = money(order.subtotal + order.delivery_charge, cur)
    kept = "".join(
        f'<input type="hidden" name="{k}" value="{e(str(v))}">'
        for k, v in form.model_dump(exclude={"heard", "change"}).items()
    )
    note = (
        '<p class="problem">Something changed since you checked (a price, or the delivery '
        "charge). Check it again.</p>"
        if changed
        else ""
    )
    body = (
        f'<p class="shop">{e(shop)}</p><h1>Check your order</h1>{note}<dl>{listed}'
        f'<dt class="total">Total</dt><dd class="total">{e(total)}</dd></dl>'
        f'<form method="post">{kept}<input type="hidden" name="heard" value="{e(read_back)}">'
        '<button class="btn">Place order</button>'
        '<button class="btn light" name="change" value="1" formnovalidate>Change</button>'
        "</form>"
    )
    return page(f"Your order: {product.name}", body)


def order_page(order: ShopOrder, shop: str, pay_link: str) -> str:
    """`/o/<id>`, linked from every receipt (D130): where the order is. Not the buyer's name,
    mobile or address: a link can be passed on."""
    e = html.escape
    cur = order.currency
    status = order.status
    body = f'<p class="shop">{e(shop)}</p><h1>Order {e(order.code)}</h1>'
    body += f"<p>{e(STATUS_WORDS[status].capitalize())}.</p>"
    if status in dict(STEPS):
        reached = [key for key, _ in STEPS].index(status)
        body += "".join(
            f'<p class="{"" if n <= reached else "dim"}">{"●" if n <= reached else "○"} {e(label)}</p>'
            for n, (_, label) in enumerate(STEPS)
        )
    if order.courier_status:
        body += f'<p class="dim">The courier: {e(order.courier_status)}</p>'
    due = money(order.total - order.paid, cur)
    rows = [
        ("Items", lines_text(order.lines)),
        ("Delivery", order.zone),
        ("Total", money(order.total, cur)),
    ] + ([("To the delivery man", due)] if status not in ("cancelled", "returned") else [])
    body += "<dl>" + "".join(f"<dt>{e(k)}</dt><dd>{e(v)}</dd>" for k, v in rows) + "</dl>"
    if status == "unpaid":
        charge = money(order.delivery_charge, cur)
        body += f'<a class="btn" href="{e(pay_link)}">Pay the delivery charge, {e(charge)}</a>'
    return page(f"Order {order.code}", body)


def not_found() -> str:
    return page("Not found", "<h1>Not found</h1><p>This isn't here any more.</p>")


def page(title: str, body: str) -> str:
    head = HEAD.replace("[title]", html.escape(title))  # the owner's words: never str.format
    return f"{head}<main>{body}</main></html>"
