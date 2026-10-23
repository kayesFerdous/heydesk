"""Cards on the messaging apps (D111): each app shows a tool's card (D110) in its own way."""

from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI
from httpx import AsyncClient

from heydesk.models import Channel
from heydesk.ports.llm import ToolCall
from heydesk.runtime import cards
from heydesk.runtime.events import Card
from heydesk.services.team import MESSENGER, TELEGRAM, WHATSAPP
from tests.conftest import SEEDS
from tests.integration import test_messenger as messenger
from tests.integration import test_telegram as telegram
from tests.integration import test_whatsapp as whatsapp
from tests.integration.test_shop import DETAILS, KURTI

PAY = "https://heydesk.test/pay/r1"
RECEIPT = Card(
    **cards.receipt(
        "Order saved",
        "O-106",
        "Waiting for payment",
        [cards.row("Items", "2 × Cotton Kurti")],
        cards.amount(190000, "BDT"),
        PAY,
    )
)
TRACKING = Card(
    **cards.tracking(
        "O-106",
        [("new", "Received"), ("confirmed", "Confirmed"), ("shipped", "With the courier")],
        "confirmed",
        "Confirmed: it's being packed.",
    )
)
DOCTOR = {
    "name": "Dr. Imran Hasan",
    "speciality": "Medicine",
    "days": "Sun, Tue",
    "hours": "5:00 pm–9:00 pm",
    "fee": cards.amount(80000, "BDT"),
    "book": "Book with Dr. Imran Hasan",
}
DOCTORS = Card(**cards.doctors([DOCTOR]))
ABOUT = "Medicine · Sun, Tue · 5:00 pm–9:00 pm · ৳800"


def channel(kind: str) -> Channel:
    """A connected channel as `_send_card` reads it; not saved."""
    now = datetime.now(UTC)
    return Channel(
        id="c1",
        workspace_id="w1",
        agent_id="a1",
        kind=kind,
        token="t",
        address="3001",
        created_at=now,
    )


def place_order_script(app: FastAPI) -> None:
    llm = app.state.services.conversations.runtime.llm
    llm.script = [
        ToolCall("c1", "update_cart", {"lines": [KURTI]}),
        ToolCall("c2", "place_order", DETAILS),
        "That's 1 Cotton Kurti. Shall I place it?",
    ]


async def shop(app: FastAPI) -> None:
    await app.state.services.agents.ensure_dev_agent("shop", SEEDS / "shop.yaml")


async def test_a_summary_on_telegram_carries_yes_and_change(
    app: FastAPI, client: AsyncClient
) -> None:
    await shop(app)
    path, secret = await telegram.connect(app, client)
    place_order_script(app)

    await telegram.send(client, path, secret, telegram.message("order it"))

    fake: Any = app.state.services.channels.telegram
    assert fake.sent == [(str(telegram.CHAT), "That's 1 Cotton Kurti. Shall I place it?")]
    assert fake.choices == []  # the words go without buttons: they're under the card
    [(_, html, keys)] = fake.cards
    assert html.startswith("<b>")
    assert "1 × Cotton Kurti (M, Black) — ৳950" in html and "<b>Total: ৳1,010</b>" in html
    assert keys == [{"title": "Yes", "choice": "Yes"}, {"title": "Change", "choice": "Change"}]


async def test_a_summary_on_messenger_and_whatsapp_is_text_with_the_buttons(
    app: FastAPI, client: AsyncClient
) -> None:
    await shop(app)
    await messenger.connect(client)
    await whatsapp.connect(client)
    services = app.state.services

    place_order_script(app)
    await messenger.post(client, messenger.message("order it"))
    place_order_script(app)
    await whatsapp.post(client, whatsapp.message("order it"))

    for fake, to in [
        (services.channels.messenger, messenger.PSID),
        (whatsapp.fake(app), whatsapp.FROM),
    ]:
        words, card = fake.sent
        assert words == (to, "That's 1 Cotton Kurti. Shall I place it?")
        assert "1 × Cotton Kurti (M, Black) — ৳950" in card[1] and "Total: ৳1,010" in card[1]
        assert fake.choices == [(to, ["Yes", "Change"])]  # under the card, the last message
    assert services.channels.messenger.sent[1][1].startswith("Your order")  # no bold
    assert whatsapp.fake(app).sent[1][1].startswith("*")  # WhatsApp's bold


async def test_a_receipt_has_a_pay_button_on_each_app(app: FastAPI) -> None:
    channels = app.state.services.channels

    await channels._send_card(channel(TELEGRAM), "555", RECEIPT, [])
    await channels._send_card(channel(MESSENGER), "9001", RECEIPT, [])
    await channels._send_card(channel(WHATSAPP), "880", RECEIPT, [])

    [(_, html, keys)] = channels.telegram.cards
    assert html == (
        "<b>Order saved · O-106</b>\nStatus: Waiting for payment\n"
        "Items: 2 × Cotton Kurti\n<b>Total: ৳1,900</b>"
    )
    assert keys == [{"title": "💳 Pay now", "url": PAY}]
    [(_, text, title, url)] = channels.messenger.links
    assert text.startswith("Order saved · O-106\n") and (title, url) == ("Pay now", PAY)
    [(_, text, title, url)] = channels.whatsapp.links
    assert text.startswith("*Order saved · O-106*\n") and (title, url) == ("Pay now", PAY)


async def test_tracking_is_its_steps(app: FastAPI) -> None:
    channels = app.state.services.channels

    await channels._send_card(channel(TELEGRAM), "555", TRACKING, [])
    await channels._send_card(channel(WHATSAPP), "880", TRACKING, [])

    steps = "✅ Received → 🔵 Confirmed → ⚪ With the courier"
    assert channels.telegram.cards == [
        ("555", f"<b>Order O-106</b>\n{steps}\nConfirmed: it's being packed.", [])
    ]
    assert channels.whatsapp.sent == [
        ("880", f"*Order O-106*\n{steps}\nConfirmed: it's being packed.")
    ]


async def test_doctors_have_a_book_button_each(app: FastAPI) -> None:
    channels = app.state.services.channels

    await channels._send_card(channel(TELEGRAM), "555", DOCTORS, [])
    await channels._send_card(channel(MESSENGER), "9001", DOCTORS, [])
    await channels._send_card(channel(WHATSAPP), "880", DOCTORS, [])

    book = "Book with Dr. Imran Hasan"
    assert channels.telegram.cards == [
        (
            "555",
            "<b>Dr. Imran Hasan</b> — Medicine\nSun, Tue · 5:00 pm–9:00 pm · ৳800",
            [{"title": book, "choice": book}],
        )
    ]
    shown = {"name": "Dr. Imran Hasan", "price": ABOUT, "choice": book, "button": "Book"}
    assert channels.messenger.cards == [("9001", [shown])]
    row = {"id": book, "title": "Dr. Imran Hasan", "description": ABOUT}
    assert channels.whatsapp.lists == [("880", "Choose a doctor to book.", [row])]


async def test_a_tapped_book_is_its_words(app: FastAPI, client: AsyncClient) -> None:
    llm = app.state.services.conversations.runtime.llm
    book = "Book with Dr. Imran Hasan"
    path, secret = await telegram.connect(app, client)
    await whatsapp.connect(client)
    await messenger.connect(client)

    chat = {"id": telegram.CHAT, "type": "private"}
    tapped = {"id": "q1", "data": book, "message": {"chat": chat}}
    await telegram.send(client, path, secret, {"update_id": 2, "callback_query": tapped})
    assert llm.requests[-1].messages[-1]["content"] == book

    row = {"type": "list_reply", "list_reply": {"id": book, "title": "Dr. Imran Hasan"}}
    await whatsapp.post(client, whatsapp.message(type="interactive", interactive=row))
    assert llm.requests[-1].messages[-1]["content"] == book

    await messenger.post(client, messenger.postback(book))
    assert llm.requests[-1].messages[-1]["content"] == book
