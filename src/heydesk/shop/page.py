"""A product's public page: `/p/<the agent's public key>/<code>`, opened from a product
card in any chat. Server-rendered HTML with only what a buyer may see: photos, name, price,
the sizes and colours in stock, the description. No script runs on it: the photo gallery and
the size and colour chips are radio buttons and CSS.

Its *Buy now* form posts back to it: first a page to check the order, read back as the
chat reads it, then *Place order*, the buyer's yes. The order's own page is `/o/<id>`."""

import html

from heydesk.runtime.speech import money
from heydesk.shop.districts import DISTRICTS
from heydesk.shop.models import ShopOrder
from heydesk.shop.values import (
    STATUS_WORDS,
    STEPS,
    BuyForm,
    NewOrder,
    Product,
    Zone,
    lines_text,
)

FEW = 5  # "Only 3 left" at this many or fewer

STYLE = """
:root{--bg:#f6f4f0;--card:#fff;--ink:#16130f;--soft:#5f5a53;--faint:#8d877e;--line:#e7e2da;
--tint:#f1eee8;--sale:#b42318;--ok:#1d7a4c;--warn:#a15c07;--r:18px}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);-webkit-font-smoothing:antialiased;
font:16px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Noto Sans Bengali",
"Helvetica Neue",Arial,sans-serif}
a{color:inherit}
.bar{position:sticky;top:0;z-index:5;background:rgba(246,244,240,.88);
backdrop-filter:saturate(1.4) blur(12px);-webkit-backdrop-filter:saturate(1.4) blur(12px);
border-bottom:1px solid var(--line)}
.bar>div{max-width:72rem;margin:0 auto;padding:.7rem 1rem;display:flex;align-items:center;
gap:.7rem}
.mono{width:2.1rem;height:2.1rem;border-radius:50%;background:var(--ink);color:#fff;flex:none;
display:grid;place-items:center;font-weight:650;font-size:.95rem}
.shop{font-weight:620;letter-spacing:-.01em;flex:1;min-width:0;overflow:hidden;
text-overflow:ellipsis;white-space:nowrap}
.back{font-size:.9rem;text-decoration:none;padding:.45rem .9rem;border:1px solid var(--line);
border-radius:99px;background:var(--card);white-space:nowrap}
.back:hover{border-color:var(--ink)}
main{max-width:72rem;margin:0 auto;padding:1rem 1rem 4rem}
.grid{display:grid;gap:1.5rem}
@media(min-width:56rem){.grid{grid-template-columns:minmax(0,1.1fr) minmax(0,1fr);gap:3.5rem;
padding-top:1.5rem}.gallery{position:sticky;top:5.5rem;align-self:start}}
.gallery>input{position:absolute;opacity:0;pointer-events:none}
.stage{position:relative;aspect-ratio:1;border-radius:var(--r);overflow:hidden;
background:var(--tint)}
.stage img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;opacity:0;
transition:opacity .35s ease}
.stage .s1{opacity:1}
.noimg{aspect-ratio:1;border-radius:var(--r);background:var(--tint);display:grid;
place-items:center;color:var(--faint);font-size:4rem;font-weight:600}
.thumbs{display:flex;gap:.6rem;margin-top:.7rem}
.thumbs label{width:4.5rem;aspect-ratio:1;border-radius:12px;overflow:hidden;cursor:pointer;
outline:2px solid transparent;outline-offset:2px;opacity:.6;transition:opacity .2s}
.thumbs label:hover{opacity:1}
.thumbs img{width:100%;height:100%;object-fit:cover;display:block}
[[gallery]]
.eyebrow{margin:0;font-size:.78rem;letter-spacing:.12em;text-transform:uppercase;
color:var(--faint);font-weight:600}
h1{font-size:clamp(1.7rem,4vw,2.5rem);line-height:1.15;letter-spacing:-.025em;
margin:.35rem 0 .9rem;font-weight:680}
.price{display:flex;align-items:baseline;flex-wrap:wrap;gap:.3rem .7rem;margin:0}
.now{font-size:1.65rem;font-weight:680;letter-spacing:-.02em;font-variant-numeric:tabular-nums}
.was{color:var(--faint);text-decoration:line-through;font-size:1.05rem}
.save{font-size:.78rem;font-weight:700;color:var(--sale);background:#fdecea;padding:.2rem .6rem;
border-radius:99px}
.pill{display:inline-flex;align-items:center;gap:.45rem;margin:1rem 0 0;font-size:.9rem;
font-weight:600}
.pill i{width:.55rem;height:.55rem;border-radius:50%;background:currentColor}
.ok{color:var(--ok)}.few{color:var(--warn)}.gone{color:var(--sale)}
hr{border:0;border-top:1px solid var(--line);margin:1.5rem 0}
h2{font-size:.82rem;letter-spacing:.08em;text-transform:uppercase;color:var(--soft);
margin:0 0 .6rem;font-weight:650}
.chips{display:flex;flex-wrap:wrap;gap:.5rem;margin:0 0 1.2rem}
.chips span,.pick span{display:inline-block;min-width:2.8rem;text-align:center;
padding:.5rem .95rem;border:1px solid var(--line);border-radius:12px;background:var(--card);
font-weight:550;font-size:.95rem}
.about{white-space:pre-line;color:#2e2a25;margin:0}
.video{display:inline-flex;align-items:center;gap:.5rem;margin-top:1rem;font-weight:600;
text-decoration:none;padding:.55rem 1rem;border-radius:99px;background:var(--tint)}
.video:hover{background:var(--line)}
.facts{list-style:none;padding:0;margin:0;display:grid;gap:.75rem}
.facts li{display:flex;gap:.75rem;align-items:flex-start;font-size:.95rem}
.facts svg{flex:none;margin-top:.15rem;color:var(--soft)}
.facts b{font-weight:600}.facts small{display:block;color:var(--faint);font-size:.85rem}
.btn{display:block;width:100%;padding:1rem;border-radius:14px;border:0;background:var(--ink);
color:#fff;font:inherit;font-weight:650;font-size:1.02rem;text-align:center;
text-decoration:none;cursor:pointer;transition:transform .15s,background .15s}
.btn:hover{background:#2c2721}.btn:active{transform:scale(.99)}
.light{background:var(--card);color:var(--ink);border:1px solid var(--line);margin-top:.6rem}
.light:hover{background:var(--tint)}
details.buy{margin-top:1.5rem}
details.buy>summary{list-style:none}
details.buy>summary::-webkit-details-marker{display:none}
details.buy[open]>summary{display:none}
.panel{background:var(--card);border:1px solid var(--line);border-radius:var(--r);
padding:1.25rem}
details.buy form{display:grid;gap:1rem}
.panel h3{margin:0;font-size:1.1rem;letter-spacing:-.01em}
label,.legend{display:block;font-size:.88rem;font-weight:600;color:var(--soft)}
input,select,textarea{display:block;width:100%;margin-top:.35rem;padding:.75rem .85rem;
font:inherit;color:var(--ink);border:1px solid var(--line);border-radius:12px;
background:var(--card);transition:border-color .15s}
input:focus,select:focus,textarea:focus{outline:0;border-color:var(--ink)}
.two{display:grid;grid-template-columns:1fr 1fr;gap:.8rem}
fieldset{border:0;padding:0;margin:0}
.pick{display:flex;flex-wrap:wrap;gap:.5rem;margin-top:.4rem}
.pick label{margin:0;color:var(--ink);cursor:pointer}
.pick input{position:absolute;opacity:0;width:1px;height:1px;margin:0}
.pick input:checked+span{background:var(--ink);color:#fff;border-color:var(--ink)}
.pick input:focus-visible+span{outline:2px solid var(--ink);outline-offset:2px}
.problem{margin:0;padding:.75rem .9rem;border-radius:12px;background:#fdecea;color:var(--sale);
font-weight:600;font-size:.92rem}
.dim{color:var(--faint);font-size:.88rem;margin:0}
.chat{margin-top:1rem;padding:1rem 1.1rem;border-radius:var(--r);background:var(--tint);
font-size:.93rem;color:var(--soft)}
.chat b{color:var(--ink);font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
font-size:.88rem;background:var(--card);padding:.1rem .4rem;border-radius:6px}
.narrow{max-width:34rem;margin:0 auto;padding-top:1.5rem}
.item{display:flex;gap:1rem;align-items:center;margin-bottom:1.25rem}
.item img{width:4.5rem;height:4.5rem;border-radius:12px;object-fit:cover;background:var(--tint)}
.item p{margin:0}
dl{display:grid;grid-template-columns:auto 1fr;gap:.65rem 1.25rem;margin:0}
dt{color:var(--faint);font-size:.9rem}dd{margin:0;font-weight:520}
.total{border-top:1px solid var(--line);padding-top:.8rem;font-weight:700;color:var(--ink);
font-size:1.1rem}
.steps{list-style:none;padding:0;margin:1.25rem 0;display:grid;gap:0}
.steps li{position:relative;padding:0 0 1.1rem 2rem;color:var(--faint)}
.steps li::before{content:"";position:absolute;left:.3rem;top:.35rem;width:.75rem;height:.75rem;
border-radius:50%;border:2px solid var(--line);background:var(--card)}
.steps li::after{content:"";position:absolute;left:.66rem;top:1.25rem;bottom:.1rem;width:2px;
background:var(--line)}
.steps li:last-child::after{display:none}
.steps .done{color:var(--ink);font-weight:600}
.steps .done::before{background:var(--ink);border-color:var(--ink)}
.steps .done::after{background:var(--ink)}
.status{font-size:1.05rem;margin:0 0 .25rem}
.foot{text-align:center;color:var(--faint);font-size:.8rem;margin-top:3rem}
"""
# A browser shows the page and its photos, and nothing else loads or runs
POLICY = (
    "default-src 'none'; img-src https: http: data:; style-src 'unsafe-inline'; "
    "form-action 'self'"  # the Buy now form posts only back to us
)
# The icons beside the delivery lines; drawn inline, so nothing loads
TRUCK = (
    '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
    'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7h11v9H3z'
    'M14 10h4l3 3v3h-7"/><circle cx="7" cy="17.5" r="1.8"/><circle cx="17" cy="17.5" r="1.8"/>'
    "</svg>"
)
CASH = (
    '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
    'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="6" '
    'width="18" height="12" rx="2"/><circle cx="12" cy="12" r="2.5"/></svg>'
)


def product_page(
    product: Product,
    shop: str,
    currency: str,
    back: str,
    zones: list[Zone] | None = None,
    form: BuyForm | None = None,
    problem: str = "",
    image: str = "",
) -> str:
    """`back` is the chat's own link (Messenger, Telegram), or "" where closing the page goes
    back to it (the website chat, WhatsApp). `zones`: where the shop delivers, for the *Buy
    now* form; `form` and `problem`, the buyer's details again with what to fix. `image`: the
    first photo's whole link, for the preview an app shows of a pasted link."""
    e = html.escape
    sold_out = product.sold_out()
    price = f'<span class="now">{e(money(product.price, currency))}</span>'
    if product.was_price and product.was_price > product.price:
        off = round((product.was_price - product.price) * 100 / product.was_price)
        price += f'<span class="was">{e(money(product.was_price, currency))}</span>'
        price += f'<span class="save">Save {off}%</span>' if off else ""
    if sold_out:
        stock = '<p class="pill gone"><i></i>Sold out</p>'
    elif not product.variants and product.stock is not None and product.stock <= FEW:
        stock = f'<p class="pill few"><i></i>Only {product.stock} left</p>'
    else:
        stock = '<p class="pill ok"><i></i>In stock</p>'

    info = f'<p class="eyebrow">{e(product.category)}</p>' if product.category else ""
    info += f'<h1>{e(product.name)}</h1><p class="price">{price}</p>{stock}<hr>'
    if not sold_out:
        for what, label in (("size", "Sizes"), ("colour", "Colours")):
            left = product.in_stock(what)
            if left:
                chips = "".join(f"<span>{e(choice)}</span>" for choice in left)
                info += f'<h2>{label} in stock</h2><div class="chips">{chips}</div>'
    if product.description:
        info += f'<h2>About this</h2><p class="about">{e(product.description)}</p>'
    for n, link in enumerate(product.videos, 1):  # links out, so nothing loads here
        name = "Watch the video" if len(product.videos) == 1 else f"Watch video {n}"
        info += f'<a class="video" href="{e(link)}" target="_blank" rel="noopener">▶ {name}</a>'
    if zones and not sold_out:
        info += buy_form(product, zones, currency, form or BuyForm(), problem)
    order = f"Order [{product.code}]"
    info += f'<div class="chat">Prefer to chat? Go back and say <b>{e(order)}</b>.'
    if back:
        info += f'<a class="btn light" href="{e(back)}">Back to the chat</a>'
    info += "</div>"
    if zones:
        info += f"<hr>{delivery(zones, currency)}"

    body = f'<div class="grid">{gallery(product)}<div>{info}</div></div>'
    share = {
        "og:title": product.name,
        "og:description": product.description[:200] or shop,
        "og:site_name": shop,
        "og:type": "product",
    }
    if image:
        share["og:image"] = image
    meta = "".join(f'<meta property="{k}" content="{e(v)}">' for k, v in share.items())
    return page(f"{product.name} · {shop}", body, shop, back, meta)


def gallery(product: Product) -> str:
    """The photos: a big one, and a thumbnail each to switch it when there are more. A radio
    button per photo, so CSS swaps them without a script."""
    e = html.escape
    if not product.photos:
        return f'<div class="gallery"><div class="noimg">{e(product.name[:1].upper())}</div></div>'
    many = len(product.photos) > 1
    radios = "".join(
        f'<input type="radio" name="photo" id="g{n}" aria-label="Photo {n}"'
        f"{' checked' if n == 1 else ''}>"
        for n in range(1, len(product.photos) + 1)
    )
    stage = "".join(
        f'<img class="s{n}" src="{e(url)}" alt="{e(product.name)}">'
        for n, url in enumerate(product.photos, 1)
    )
    thumbs = "".join(
        f'<label for="g{n}"><img src="{e(url)}" alt=""></label>'
        for n, url in enumerate(product.photos, 1)
    )
    return (
        '<div class="gallery">'
        + (radios if many else "")
        + f'<div class="stage">{stage}</div>'
        + (f'<div class="thumbs">{thumbs}</div>' if many else "")
        + "</div>"
    )


def delivery(zones: list[Zone], currency: str) -> str:
    """Each area's charge and days, and that the rest is paid at the door."""
    e = html.escape
    rows = "".join(
        f"<li>{TRUCK}<span><b>{e(z.name)}</b> · {e(money(z.charge, currency) if z.charge else 'free')}"
        + (f"<small>Usually {e(z.days)}</small>" if z.days else "")
        + "</span></li>"
        for z in zones
    )
    cash = (
        f"<li>{CASH}<span><b>Cash on delivery</b><small>Pay when it reaches you</small></span></li>"
    )
    return f'<h2>Delivery</h2><ul class="facts">{rows}{cash}</ul>'


def buy_form(
    product: Product, zones: list[Zone], currency: str, form: BuyForm, problem: str
) -> str:
    """*Buy now*: a form that opens in place (no script), posted back to this page."""
    e = html.escape

    def choose(what: str, label: str, chosen: str) -> str:
        left = product.in_stock(what)
        if not left:
            return ""
        chosen = chosen or (left[0] if len(left) == 1 else "")
        chips = "".join(
            f'<label><input type="radio" name="{what}" value="{e(c)}"'
            f"{' checked' if c == chosen else ''}{' required' if n == 0 else ''}>"
            f"<span>{e(c)}</span></label>"
            for n, c in enumerate(left)
        )
        return f'<fieldset><legend class="legend">{label}</legend><div class="pick">{chips}</div></fieldset>'

    def field(name: str, label: str, value: str, kind: str = "text", more: str = "") -> str:
        return (
            f'<label>{label}<input name="{name}" type="{kind}" value="{e(value)}" {more}></label>'
        )

    districts = "".join(
        f"<option{' selected' if d == (form.district or 'Dhaka') else ''}>{e(d)}</option>"
        for d in DISTRICTS
    )
    shown = f'<p class="problem">{e(problem)}</p>' if problem else ""
    return (
        f'<details class="buy"{" open" if problem else ""}>'
        '<summary class="btn">Buy now</summary>'
        '<form method="post" class="panel"><h3>Your order</h3>'
        + shown
        + choose("size", "Size", form.size)
        + choose("colour", "Colour", form.colour)
        + field("quantity", "How many", str(form.quantity), "number", 'min="1" max="20" required')
        + '<div class="two">'
        + field("name", "Your name", form.name, more='required maxlength="100"')
        + field("phone", "Mobile", form.phone, "tel", 'required placeholder="01712345678"')
        + "</div>"
        + f'<label>Address<textarea name="address" required maxlength="300" rows="2" '
        f'placeholder="House, road, area">{e(form.address)}</textarea></label>'
        + f'<label>District<select name="district" required>{districts}</select></label>'
        + field("note", "Note (if any)", form.note, more='maxlength="500"')
        + '<button class="btn">Check my order</button>'
        + '<p class="dim">Nothing is ordered until you check it and tap Place order.</p>'
        + "</form></details>"
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
    """The order read back, as the chat would; *Place order* is the yes. The form's
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
    photo = f'<img src="{e(product.photos[0])}" alt="">' if product.photos else ""
    body = (
        '<div class="narrow"><h1>Check your order</h1>'
        f'<div class="panel">{note}<div class="item">{photo}<div><p><b>{e(product.name)}</b></p>'
        f'<p class="dim">{e(money(product.price, cur))} each</p></div></div>'
        f'<dl>{listed}<dt class="total">Total</dt><dd class="total">{e(total)}</dd></dl></div>'
        f'<form method="post" style="margin-top:1rem">{kept}'
        f'<input type="hidden" name="heard" value="{e(read_back)}">'
        '<button class="btn">Place order</button>'
        '<button class="btn light" name="change" value="1" formnovalidate>Change</button>'
        "</form></div>"
    )
    return page(f"Your order: {product.name}", body, shop)


def order_page(order: ShopOrder, shop: str, pay_link: str) -> str:
    """`/o/<id>`, linked from every receipt: where the order is. Not the buyer's name,
    mobile or address: a link can be passed on."""
    e = html.escape
    cur = order.currency
    status = order.status
    head, _, rest = STATUS_WORDS[status].partition(":")  # "received: the shop will call…"
    body = f'<p class="eyebrow">Order {e(order.code)}</p><h1>{e(head.capitalize())}</h1>'
    if rest:
        body += f'<p class="status">{e(rest.strip().capitalize())}.</p>'
    if order.courier_status:
        body += f'<p class="dim">The courier: {e(order.courier_status)}</p>'
    if status in dict(STEPS):
        reached = [key for key, _ in STEPS].index(status)
        body += (
            '<ol class="steps">'
            + "".join(
                f'<li class="{"done" if n <= reached else ""}">{e(label)}</li>'
                for n, (_, label) in enumerate(STEPS)
            )
            + "</ol>"
        )
    due = money(order.total - order.paid, cur)
    rows = [
        ("Items", lines_text(order.lines)),
        ("Delivery", order.zone),
        ("Total", money(order.total, cur)),
    ] + ([("To the delivery man", due)] if status not in ("cancelled", "returned") else [])
    listed = "".join(f"<dt>{e(k)}</dt><dd>{e(v)}</dd>" for k, v in rows)
    body += f'<div class="panel"><dl>{listed}</dl></div>'
    if status == "unpaid":
        charge = money(order.delivery_charge, cur)
        body += (
            f'<a class="btn" style="margin-top:1rem" href="{e(pay_link)}">'
            f"Pay the delivery charge, {e(charge)}</a>"
        )
    return page(f"Order {order.code}", f'<div class="narrow">{body}</div>', shop)


def not_found() -> str:
    return page(
        "Not found",
        '<div class="narrow"><h1>Not found</h1><p class="dim">This isn\'t here any more.</p></div>',
    )


def page(title: str, body: str, shop: str = "", back: str = "", meta: str = "") -> str:
    """The page around `body`: the shop's bar on top (its initial, its name, back to the
    chat), and the styles, the gallery's one rule per photo among them."""
    e = html.escape
    shows = "".join(
        f"#g{n}:checked~.stage .s{n}{{opacity:1}}#g{n}:checked~.stage img:not(.s{n}){{opacity:0}}"
        f'#g{n}:checked~.thumbs label[for="g{n}"]{{opacity:1;outline-color:var(--ink)}}'
        for n in range(1, 4)
    )
    style = STYLE.replace("[[gallery]]", shows)  # the owner's words never reach this
    bar = ""
    if shop:
        link = f'<a class="back" href="{e(back)}">Back to the chat</a>' if back else ""
        bar = (
            f'<header class="bar"><div><span class="mono">{e(shop[:1].upper())}</span>'
            f'<span class="shop">{e(shop)}</span>{link}</div></header>'
        )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="robots" content="noindex">'
        f"<title>{e(title)}</title>{meta}<style>{style}</style></head>"
        f"<body>{bar}<main>{body}</main></body></html>"
    )
