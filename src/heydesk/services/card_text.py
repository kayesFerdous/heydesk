"""How a tool's card, a product card and the reply's buttons read on each messaging app. Only
words are made here, nothing is sent: `services/channels.py` sends them."""

import html
from typing import Any

from heydesk.runtime.events import Card
from heydesk.services.team import TELEGRAM, WHATSAPP


def buttons(choices: list[str]) -> list[str]:
    """The choices a button can carry: Messenger takes 13 quick replies of 20 characters,
    and Telegram's button data is 64 bytes. A longer one is left out, so it's typed instead."""
    return [c for c in choices if len(c) <= 20 and len(c.encode()) <= 64][:13]


def card_text(card: Card, channel: str) -> str:
    """A card as a message's words, a line each, its title in bold: Telegram's HTML
    (so every value is escaped), WhatsApp's *stars*; Messenger has no bold."""

    def esc(s: str) -> str:  # a name or an address could hold a "<"
        return html.escape(s, quote=False) if channel == TELEGRAM else s

    def bold(s: str) -> str:
        if channel == TELEGRAM:
            return f"<b>{s}</b>"
        return f"*{s}*" if channel == WHATSAPP else s

    data = card.data
    said: list[str] = []
    if card.kind == "tracking":
        steps = data["steps"]
        marks = [step_icon(s, i == len(steps) - 1 and len(steps) > 1) for i, s in enumerate(steps)]
        said.append(bold(esc(f"Order {data['number']}")))
        said.append(" → ".join(f"{m} {esc(s['label'])}" for m, s in zip(marks, steps, strict=True)))
        if data["note"]:
            said.append(esc(data["note"]))
        return "\n".join(said)
    if card.kind == "receipt":
        said.append(bold(esc(f"{data['title']} · {data['number']}")))
        said.append(esc(f"Status: {data['status']}"))
    else:  # a summary
        said.append(bold(esc(data["title"])))
        for line in data["lines"]:
            label = f"{line['qty']} × {line['label']}" if "qty" in line else line["label"]
            cost = f" — {line['amount']['text']}" if "amount" in line else ""
            said.append(esc(label + cost))
    said += [esc(f"{r['label']}: {r['value']}") for r in data["rows"]]
    if "total" in data:
        said.append(bold(esc(f"Total: {data['total']['text']}")))
    if "order_url" in data:  # the apps make a link of it
        said.append(esc(f"Follow your order: {data['order_url']}"))
    return "\n".join(said)


def step_icon(step: dict[str, Any], last: bool) -> str:
    """✅ done, 🔵 where it is now, ⚪ still to come; the last step reached is done."""
    if step["current"] and not last:
        return "🔵"
    return "✅" if step["done"] else "⚪"


def card_words(card: dict[str, str]) -> str:
    """A product card as the buyer sees it: "Cotton Shirt · ৳950"."""
    return f"{card['name']} · {card['price']}"
