"""Paying first: with `pay_first` on, an order is saved unpaid, and only a
payment the gateway vouches for, for the right amount, places it. The shop takes the delivery
charge in the areas that pay first, and that payment confirms the order."""

from typing import Any, cast

from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import text

from heydesk.adapters.payments.fake import FakePayments
from heydesk.ports.llm import ToolCall
from heydesk.ports.payments import Account, Paid
from heydesk.services import Services
from heydesk.services.agents import DEV_WORKSPACE_ID, LiveAgent
from heydesk.shop import shop_prompt
from heydesk.shop.models import ShopOrder
from heydesk.shop.tools import FindOrderInput, track_order
from tests.conftest import SEEDS, World, make_ctx

SHOP_ORDER = {"lines": [{"code": "kurti", "quantity": 1, "size": "M", "colour": "Black"}]}
SHOP_DETAILS = {
    "district": "Dhaka",
    "address": "House 12, Road 5, Mirpur 10, Dhaka",
    "name": "Kayes",
    "phone": "01712345678",
}


async def paying_first(services: Services, **settings: object) -> tuple[LiveAgent, FakePayments]:
    """The dev agent with `pay_first` on (and `settings`) and an SSLCommerz store
    connected, and its fake gateway. A shop pays first inside Dhaka only."""
    await services.agents.ensure_dev_agent(SEEDS / "shop.yaml")
    await services.agents.update(DEV_WORKSPACE_ID, {**pay_first(services), **settings})
    store = Account("nakshi-test", "store-password", True)
    await services.payments.connect(DEV_WORKSPACE_ID, "sslcommerz", store)
    agent = await services.agents.by_key("dev-shop")
    return agent, cast(FakePayments, services.payments.gateways["sslcommerz"])


def pay_first(services: Services) -> dict[str, Any]:
    """The setting that turns paying first on: each area's."""
    zones = [
        {"name": "Inside Dhaka", "charge": 6000, "districts": ["Dhaka"], "pay_first": True},
        {"name": "Outside Dhaka", "charge": 12000},
    ]
    return {"delivery": zones}


async def take(world: World, agent: LiveAgent, first: list[Any], tool: ToolCall) -> str:
    """Asks for the yes, then says it; returns the conversation id."""
    conversation = await world.say(agent, None, [*first, tool, "Shall I?"], "…")
    await world.say(agent, conversation, [tool, "Pay here…"], "yes")
    return conversation


async def found(world: World, conversation: str) -> ShopOrder:
    """The order or appointment the conversation took: its id is in the payment link."""
    said = str((await world.conversation(conversation)).messages)
    record = await world.services.payments.find(said.split("/pay/")[1][:32])
    assert record is not None
    return record


async def test_the_shop_order_waits_for_a_checked_payment_of_its_delivery_charge(
    world: World,
) -> None:
    agent, gateway = await paying_first(world.services)
    prompt = await shop_prompt(agent.config, world.store(agent))
    assert "- Inside Dhaka: ৳60 (Dhaka); paid online first" in prompt
    assert (
        "- Outside Dhaka: ৳120 (everywhere else)\n" in prompt
    )  # cash on delivery, and the shop calls
    assert "Elsewhere, orders are paid in cash on delivery, and the shop calls" in prompt
    first = [ToolCall("c1", "update_cart", SHOP_ORDER)]
    conversation = await world.say(
        agent, None, [*first, ToolCall("c2", "place_order", SHOP_DETAILS), "…"]
    )
    assert "the delivery charge ৳60 paid online first, then ৳950 in cash on delivery" in (
        await world.tool_said(conversation)
    )
    await world.say(
        agent, conversation, [ToolCall("c3", "place_order", SHOP_DETAILS), "Pay…"], "yes"
    )
    order = await found(world, conversation)
    assert order.status == "unpaid"  # held, not placed: the yes alone isn't enough
    assert f"https://heydesk.test/pay/{order.id}" in await world.tool_said(conversation)

    payments = world.services.payments
    gateway.paid["short"] = Paid(order.id, 1000, "BDT", "B1")  # ৳10, not ৳60
    gateway.paid["elsewhere"] = Paid("another-order", 6000, "BDT", "B0")  # paid, but not this
    for forged_or_short in ("made-up", "short", "elsewhere"):
        await payments.check_payment(order.id, forged_or_short)
        assert (await found(world, conversation)).status == "unpaid"

    gateway.paid["v1"] = Paid(order.id, 6000, "BDT", "B2")
    await payments.check_payment(order.id, "v1")
    await payments.check_payment(
        order.id, "v1"
    )  # the visitor's return and the gateway's notice: once is enough
    placed = await found(world, conversation)
    # the advance confirms it: no call
    assert (placed.status, placed.paid, placed.payment_ref) == ("confirmed", 6000, "B2")
    said = (await world.conversation(conversation)).messages
    assert said[-1]["by"] == "team"
    assert said[-1]["content"].startswith("✅ Payment received: ৳60 for the delivery charge.")
    assert "Your order O-101 is confirmed" in said[-1]["content"]
    assert "Pay the rest, ৳950, to the delivery man" in said[-1]["content"]
    assert "call you" not in said[-1]["content"]
    assert sum("Payment received" in str(m["content"]) for m in said) == 1

    tracked = await track_order(
        make_ctx(world.store(agent), agent=agent.config),
        FindOrderInput(code="O-101", phone="01712345678"),
    )
    assert (tracked.data["paid_online"], tracked.data["cash_on_delivery"]) == ("৳60", "৳950")


async def test_a_shop_takes_the_advance_only_in_the_areas_that_pay_first(world: World) -> None:
    agent, _ = await paying_first(world.services)
    sylhet = SHOP_DETAILS | {"district": "Sylhet", "address": "Zindabazar, Sylhet"}
    first = [ToolCall("c1", "update_cart", SHOP_ORDER)]
    conversation = await take(world, agent, first, ToolCall("c2", "place_order", sylhet))
    assert "pay_link" not in await world.tool_said(conversation)
    [order] = await world.store(agent, conversation).orders_in_conversation()
    assert (order.zone, order.status, order.total) == ("Outside Dhaka", "new", 107000)


async def test_the_payment_link_goes_to_the_gateway_and_comes_back_placed(
    app: FastAPI, client: AsyncClient
) -> None:
    services = app.state.services
    agent, gateway = await paying_first(services)
    llm = services.conversations.llm
    llm.script = [
        ToolCall("c1", "update_cart", SHOP_ORDER),
        ToolCall("c2", "place_order", SHOP_DETAILS),
        "Shall I place it?",
        ToolCall("c3", "place_order", SHOP_DETAILS),
        "Pay here…",
    ]
    first = await client.post(
        f"/api/chat/{agent.public_key}", json={"visitor_id": "test-visitor", "message": "a kurti"}
    )
    conv_id = first.text.split('"conversation_id": "')[1].split('"')[0]
    await client.post(
        f"/api/chat/{agent.public_key}",
        json={"visitor_id": "test-visitor", "message": "yes", "conversation_id": conv_id},
    )
    [order] = (await client.get("/api/shop/orders")).json()

    went = await client.get(f"/pay/{order['id']}")
    assert (went.status_code, went.headers["location"]) == (303, f"https://pay.test/{order['id']}")
    assert (gateway.started[0][1].amount, gateway.started[0][1].what) == (
        6000,
        "Delivery charge for O-101",
    )
    assert gateway.started[0][1].back_url == f"https://heydesk.test/pay/{order['id']}/back"

    failed = await client.post(f"/pay/{order['id']}/back", data={"status": "FAILED"})
    assert "didn't go through" in failed.text
    forged = await client.post(f"/pay/{order['id']}/back", data={"val_id": "x", "status": "VALID"})
    assert "didn't go through" in forged.text

    gateway.paid["v1"] = Paid(order["id"], 6000, "BDT", "B1")
    notice = await client.post(f"/api/payments/sslcommerz/{order['id']}", data={"val_id": "v1"})
    assert notice.json() == {"ok": True}
    back = await client.post(f"/pay/{order['id']}/back", data={"val_id": "v1"})
    assert "Payment received. Your order O-101 is confirmed" in back.text
    assert "Pay the rest, ৳950" in back.text
    assert (await client.get(f"/api/shop/orders/{order['id']}")).json()["paid"] == 6000
    assert "Payment received" in (await client.get(f"/pay/{order['id']}")).text  # not again
    assert (await client.get("/pay/nothing-here")).status_code == 404


async def test_without_its_own_sslcommerz_the_agent_takes_orders_as_before(world: World) -> None:
    services = world.services
    await services.agents.ensure_dev_agent(SEEDS / "shop.yaml")
    await services.agents.update(DEV_WORKSPACE_ID, pay_first(services))  # no store yet
    agent = await services.agents.by_key("dev-shop")
    assert "paid online first" not in await shop_prompt(agent.config, world.store(agent))
    assert (
        "Connect SSLCommerz or bKash to take payments first."
        in (await services.agents.get(DEV_WORKSPACE_ID)).missing
    )

    first = [ToolCall("c1", "update_cart", SHOP_ORDER)]
    conversation = await take(world, agent, first, ToolCall("c2", "place_order", SHOP_DETAILS))
    assert "pay_link" not in await world.tool_said(conversation)
    assert '"code": "O-101"' in await world.tool_said(conversation)  # placed, cash on delivery


async def test_the_owner_connects_the_business_s_own_store(
    app: FastAPI, client: AsyncClient
) -> None:
    await app.state.services.agents.ensure_dev_agent(SEEDS / "shop.yaml")
    assert (await client.get("/api/agent/sslcommerz")).status_code == 404

    refused = await client.put(
        "/api/agent/sslcommerz", json={"store_id": "bad", "password": "x", "sandbox": True}
    )
    assert refused.status_code == 400
    assert "SSLCommerz didn't accept these: Store Credential Error" in refused.text

    body = {"store_id": "nakshi-test", "password": "the-api-password", "sandbox": True}
    connected = await client.put("/api/agent/sslcommerz", json=body)
    assert connected.status_code == 200
    shown = (await client.get("/api/agent/sslcommerz")).json()
    assert (shown["store_id"], shown["sandbox"]) == ("nakshi-test", True)
    assert "password" not in shown and "the-api-password" not in str(shown)
    async with app.state.sessions() as s:
        stored = await s.scalar(text("SELECT password FROM payment_accounts"))
    assert stored and "the-api-password" not in stored  # encrypted in the database
    assert (
        await app.state.services.payments.account(DEV_WORKSPACE_ID, "sslcommerz")
    ).password == body["password"]

    assert (await client.delete("/api/agent/sslcommerz")).status_code == 204
    assert (await client.get("/api/agent/sslcommerz")).status_code == 404


BKASH = {
    "app_key": "shop-app-key",
    "app_secret": "the-app-secret",
    "username": "shop-user",
    "password": "the-bkash-password",
    "sandbox": True,
}


async def test_the_owner_connects_bkash_too(app: FastAPI, client: AsyncClient) -> None:
    await app.state.services.agents.ensure_dev_agent(SEEDS / "shop.yaml")
    assert (await client.get("/api/agent/bkash")).status_code == 404

    refused = await client.put("/api/agent/bkash", json={**BKASH, "app_key": "bad"})
    assert refused.status_code == 400
    assert "bKash didn't accept these: Invalid App Key" in refused.text

    assert (await client.put("/api/agent/bkash", json=BKASH)).status_code == 200
    shown = (await client.get("/api/agent/bkash")).json()
    assert (shown["username"], shown["sandbox"]) == ("shop-user", True)
    assert "the-app-secret" not in str(shown) and "the-bkash-password" not in str(shown)
    async with app.state.sessions() as s:
        stored = await s.scalar(text("SELECT app_secret FROM payment_accounts"))
    assert stored and "the-app-secret" not in stored  # encrypted in the database

    assert (await client.delete("/api/agent/bkash")).status_code == 204
    assert (await client.get("/api/agent/bkash")).status_code == 404


async def test_a_bkash_payment_places_the_order_once_and_only_for_its_amount(
    app: FastAPI, client: AsyncClient
) -> None:
    services = app.state.services
    agent, _ = await paying_first(services)
    await client.put("/api/agent/bkash", json=BKASH)
    bkash = cast(FakePayments, services.payments.gateways["bkash"])
    services.conversations.llm.script = [
        ToolCall("c1", "update_cart", SHOP_ORDER),
        ToolCall("c2", "place_order", SHOP_DETAILS),
        "Shall I place it?",
        ToolCall("c3", "place_order", SHOP_DETAILS),
        "Pay here…",
    ]
    chat = f"/api/chat/{agent.public_key}"
    first = await client.post(chat, json={"visitor_id": "test-visitor", "message": "a kurti"})
    conv_id = first.text.split('"conversation_id": "')[1].split('"')[0]
    await client.post(
        chat, json={"visitor_id": "test-visitor", "message": "yes", "conversation_id": conv_id}
    )
    [order] = (await client.get("/api/shop/orders")).json()
    pay = f"/pay/{order['id']}"

    choose = await client.get(pay)  # both connected: the visitor picks
    assert "Pay with bKash" in choose.text and "Pay with a card or mobile banking" in choose.text
    went = await client.get(f"{pay}?via=bkash")
    assert (went.status_code, went.headers["location"]) == (
        303,
        f"https://bkash.test/{order['id']}",
    )
    [(account, checkout)] = bkash.started
    assert (account.app_key, account.app_secret) == ("shop-app-key", "the-app-secret")
    assert checkout.back_url == f"https://heydesk.test{pay}/bkash"

    bkash.paid["P1"] = Paid(order["id"], 6000, "BDT", "TRX1")
    for outcome in ("failure", "cancel"):  # bKash's word never asked: nothing changes
        back = await client.get(f"{pay}/bkash", params={"paymentID": "P1", "status": outcome})
        assert "didn't go through" in back.text
    bkash.paid["short"] = Paid(order["id"], 1000, "BDT", "TRX0")  # ৳10, not ৳60
    short = await client.get(f"{pay}/bkash", params={"paymentID": "short", "status": "success"})
    assert "didn't go through" in short.text

    paid = await client.get(f"{pay}/bkash", params={"paymentID": "P1", "status": "success"})
    assert "Payment received. Your order O-101 is confirmed" in paid.text
    await client.get(f"{pay}/bkash", params={"paymentID": "P1", "status": "success"})  # again
    shown = (await client.get(f"/api/shop/orders/{order['id']}")).json()
    assert (shown["status"], shown["paid"], shown["payment_ref"]) == ("confirmed", 6000, "TRX1")
    told = (await services.conversations.get(DEV_WORKSPACE_ID, conv_id)).messages
    assert sum("Payment received" in str(m) for m in told) == 1  # placed once
