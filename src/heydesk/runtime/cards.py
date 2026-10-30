"""Cards: what a tool shows on a screen next to its words. The website chat draws them;
Messenger, Telegram and WhatsApp will send them their own way. Every value is Python's
(rule 1): an amount comes in minor units with its display form, a time in UTC with its display
form. A card is `{kind, data}`; there are four kinds:

- summary: the read-back before the yes.
  `{title, lines: [{label, qty?, amount?}], rows: [{label, value, at?}], total?}`
- receipt: what was just saved. `{title, number, status, rows, total?, pay_url?, order_url?}`
- tracking: where an order is. `{number, steps: [{label, done, current}], note}`
- doctors: who fits. `{doctors: [{name, speciality, days, hours, fee, book}]}`

An amount is `{minor, currency, text}`; `at` is ISO 8601 in UTC. Voice gets no cards.
"""

from datetime import UTC, datetime
from typing import Any

from heydesk.runtime.speech import money

type Card = dict[str, Any]


def amount(minor: int, currency: str) -> dict[str, Any]:
    """45000 BDT → `{minor: 45000, currency: "BDT", text: "৳450"}`; nothing to pay is "free"."""
    return {
        "minor": minor,
        "currency": currency,
        "text": money(minor, currency) if minor else "free",
    }


def line(label: str, qty: int | None = None, cost: dict[str, Any] | None = None) -> dict[str, Any]:
    """One line of a summary: "Chicken Biryani", 2, the two of them's amount."""
    found: dict[str, Any] = {"label": label}
    if qty is not None:
        found["qty"] = qty
    if cost is not None:
        found["amount"] = cost
    return found


def row(label: str, value: str, at: datetime | None = None) -> dict[str, Any]:
    """A detail: `{label: "Time", value: "Wed 30 Sep, 5:00 pm", at: "2026-09-30T11:00:00+00:00"}`."""
    found: dict[str, Any] = {"label": label, "value": value}
    if at is not None:
        found["at"] = at.astimezone(UTC).isoformat()
    return found


def summary(
    title: str,
    rows: list[dict[str, Any]],
    lines: list[dict[str, Any]] | None = None,
    total: dict[str, Any] | None = None,
) -> Card:
    data: dict[str, Any] = {"title": title, "lines": lines or [], "rows": rows}
    if total:
        data["total"] = total
    return {"kind": "summary", "data": data}


def receipt(
    title: str,
    number: str,
    status: str,
    rows: list[dict[str, Any]],
    total: dict[str, Any] | None = None,
    pay_url: str = "",
    order_url: str = "",
) -> Card:
    data: dict[str, Any] = {"title": title, "number": number, "status": status, "rows": rows}
    if total:
        data["total"] = total
    if pay_url:  # saved unpaid: the button to pay
        data["pay_url"] = pay_url
    if order_url:  # the order's own page
        data["order_url"] = order_url
    return {"kind": "receipt", "data": data}


def tracking(number: str, flow: list[tuple[str, str]], status: str, note: str = "") -> Card:
    """`flow` is the desk's steps in order, `(status, label)`. A status off it (cancelled,
    unpaid, returned) is shown plainly, as its one step."""
    statuses = [key for key, _ in flow]
    if status in statuses:
        at = statuses.index(status)
        steps = [
            {"label": label, "done": i <= at, "current": i == at}
            for i, (_, label) in enumerate(flow)
        ]
    else:
        steps = [{"label": status.replace("_", " ").capitalize(), "done": True, "current": True}]
    return {"kind": "tracking", "data": {"number": number, "steps": steps, "note": note}}


def doctors(found: list[dict[str, Any]]) -> Card:
    return {"kind": "doctors", "data": {"doctors": found}}
