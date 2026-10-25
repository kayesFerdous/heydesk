"""A product's public page (D121): `/p/<the agent's public key>/<code>`, opened from a product
card in any chat. Server-rendered HTML with only what a buyer may see: photos, name, price,
the sizes and colours in stock, the description. No script runs on it."""

import html

from heydesk.desks.shop.values import Product
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
    "color:#fff;text-align:center;text-decoration:none;font-weight:600}"
    "</style>"
)
# A browser shows the page and its photos, and nothing else loads or runs
POLICY = "default-src 'none'; img-src https: http: data:; style-src 'unsafe-inline'"


def product_page(product: Product, shop: str, currency: str, back: str) -> str:
    """`back` is the chat's own link (Messenger, Telegram), or "" where closing the page goes
    back to it (the website chat, WhatsApp)."""
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
    order = f"Order [{product.code}]"
    body += f'<div class="order">To order, go back to the chat and say <b>{e(order)}</b>.'
    if back:
        body += f'<a class="btn" href="{e(back)}">Back to the chat</a>'
    body += "</div>"
    return page(product.name, body)


def not_found() -> str:
    return page("Not found", "<h1>Not found</h1><p>This product isn't here any more.</p>")


def page(title: str, body: str) -> str:
    head = HEAD.replace("[title]", html.escape(title))  # the owner's words: never str.format
    return f"{head}<main>{body}</main></html>"
