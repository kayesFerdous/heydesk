"""Steadfast and Pathao over a mocked HTTP layer: what each booking sends, and how their
words map to the order's end."""

import json

import httpx
import pytest

from heydesk.adapters.courier.pathao import SANDBOX, Pathao
from heydesk.adapters.courier.steadfast import BASE, Steadfast
from heydesk.ports.courier import CourierAccount, CourierError, Parcel

PARCEL_ITEM_TYPE = Parcel(
    "O-101", "Rahim", "01712345678", "House 3, Road 4, Mirpur", 360, "1 Tupi", 1
)


async def test_steadfast_books_with_its_headers_and_reads_the_end() -> None:
    sent: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        if request.url.path.endswith("create_order"):
            return httpx.Response(200, json={"status": 200, "consignment": {"consignment_id": 77}})
        return httpx.Response(200, json={"status": 200, "delivery_status": "partial_delivered"})

    steadfast = Steadfast()
    steadfast.client = httpx.AsyncClient(transport=httpx.MockTransport(answer))
    account = CourierAccount("steadfast", "k", "s")
    assert (await steadfast.book(account, PARCEL_ITEM_TYPE)) == "77"
    body = json.loads(sent[0].content)
    assert str(sent[0].url) == f"{BASE}/create_order"
    assert (sent[0].headers["Api-Key"], sent[0].headers["Secret-Key"]) == ("k", "s")
    assert (body["invoice"], body["cod_amount"], body["recipient_phone"]) == (
        "O-101",
        360,
        "01712345678",
    )
    tracked = await steadfast.status(account, "77")
    assert (tracked.status_text, tracked.final_status) == ("partial_delivered", "delivered")


async def test_pathao_logs_in_once_and_books_without_a_city() -> None:
    sent: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        path = request.url.path
        if path.endswith("issue-token"):
            return httpx.Response(200, json={"access_token": "t1", "expires_in": 3600})
        if path.endswith("stores"):
            stores = [{"store_id": 5, "is_active": 0}, {"store_id": 9, "is_active": 1}]
            return httpx.Response(200, json={"data": {"data": stores}})
        if path.endswith("orders"):
            return httpx.Response(200, json={"data": {"consignment_id": "DL1"}})
        return httpx.Response(200, json={"data": {"order_status_slug": "Return"}})

    pathao = Pathao()
    pathao.client = httpx.AsyncClient(transport=httpx.MockTransport(answer))
    account = CourierAccount("pathao", "id", "secret", "me@shop.com", "pw", sandbox=True)
    assert await pathao.verify_account(account) == 9  # the first active store
    account = CourierAccount("pathao", "id", "secret", "me@shop.com", "pw", 9, sandbox=True)
    assert (await pathao.book(account, PARCEL_ITEM_TYPE)) == "DL1"
    tracked = await pathao.status(account, "DL1")
    assert (tracked.status_text, tracked.final_status) == ("Return", "returned")

    assert [r.url.path.rsplit("/", 1)[-1] for r in sent].count("issue-token") == 1
    assert all(str(r.url).startswith(SANDBOX) for r in sent)
    booking = json.loads(sent[2].content)
    assert "recipient_city" not in booking  # Pathao finds it from the address
    assert (booking["store_id"], booking["amount_to_collect"]) == (9, 360)
    assert sent[2].headers["Authorization"] == "Bearer t1"


async def test_steadfast_says_when_the_account_isnt_active_yet() -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="Account is not active!")  # its keys read the balance

    steadfast = Steadfast()
    steadfast.client = httpx.AsyncClient(transport=httpx.MockTransport(answer))
    with pytest.raises(CourierError, match="isn't active yet"):
        await steadfast.book(CourierAccount("steadfast", "k", "s"), PARCEL_ITEM_TYPE)
