"""Paying first (D47, D48): with `pay_first` on, what a desk takes is saved unpaid, and only a
payment the gateway vouches for, for the right amount, places it. The shop takes the delivery
charge in the areas that pay first, and that payment confirms the order (D92); the restaurant
takes its delivery charge, the clinic a deposit the owner sets."""

from typing import Any, cast

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import text

from heydesk.adapters.payments.fake import FakePayments
from heydesk.desks.clinic import CLINIC
from heydesk.desks.restaurant import RESTAURANT
from heydesk.desks.shop import SHOP
from heydesk.desks.shop.tools import FindOrderInput, track_order
from heydesk.errors import InvalidConfig
from heydesk.ports.llm import ToolCall
from heydesk.ports.payments import Paid
from heydesk.services import Services
from heydesk.services.agents import DEV_WORKSPACE_ID, LiveAgent
from heydesk.services.payments import Payable
from tests.conftest import SEEDS, World, make_ctx

YES = {"read_back": 0.95, "said_yes": 0.95}
NO = {"read_back": 0.1, "said_yes": 0.1}
SHOP_ORDER = {"lines": [{"code": "kurti", "quantity": 1, "size": "M", "colour": "Black"}]}
SHOP_DETAILS = {
    "area": "inside dhaka",
    "address": "House 12, Road 5, Mirpur 10, Dhaka",
    "name": "Kayes",
    "phone": "01712345678",
}


async def paying_first(
    services: Services, kind: str, **settings: object
) -> tuple[LiveAgent, FakePayments]:
    """The dev agent as a `kind` with `pay_first` on (and `settings`) and an SSLCommerz store
    connected, and its fake gateway. A shop pays first inside Dhaka only."""
    await services.agents.ensure_dev_agent(kind, SEEDS / f"{kind}.yaml")
    await services.agents.update(DEV_WORKSPACE_ID, {**pay_first(services, kind), **settings})
    await services.payments.connect(DEV_WORKSPACE_ID, "nakshi-test", "store-password", True)
    agent = await services.agents.by_key(f"dev-{kind}")
    return agent, cast(FakePayments, services.payments.gateway)


def pay_first(services: Services, kind: str) -> dict[str, Any]:
    """The setting that turns paying first on: the shop's is each area's (D92)."""
    if kind != "shop":
        return {"pay_first": True}
    zones = [
        {"name": "Inside Dhaka", "charge": 6000, "places": ["Mirpur"], "pay_first": True},
        {"name": "Outside Dhaka", "charge": 12000, "places": ["Sylhet"]},
    ]
    return {"delivery": zones}


async def take(world: World, agent: LiveAgent, first: list[Any], tool: ToolCall) -> str:
    """Asks for the yes, then says it; returns the conversation id."""
    world.decider.script = [NO, YES]
    conv = await world.say(agent, None, [*first, tool, "Shall I?"], "…")
    await world.say(agent, conv, [tool, "Pay here…"], "yes")
    return conv


async def found(world: World, conv: str) -> Payable:
    """The order or appointment the conversation took: its id is in the payment link."""
    said = str((await world.conversation(conv)).messages)
    record = await world.services.payments.find(said.split("/pay/")[1][:32])
    assert record is not None
    return record


async def test_the_shop_order_waits_for_a_checked_payment_of_its_delivery_charge(
    world: World,
) -> None:
    agent, gateway = await paying_first(world.services, "shop")
    prompt = await SHOP.prompt(agent.config, world.store(agent))
    assert "- Inside Dhaka: ৳60 (Mirpur); paid online first" in prompt
    assert "- Outside Dhaka: ৳120 (Sylhet)\n" in prompt  # cash on delivery, and the shop calls
    assert "Elsewhere, orders are paid in cash on delivery, and the shop calls" in prompt
    first = [ToolCall("c1", "update_cart", SHOP_ORDER)]
    conv = await world.say(agent, None, [*first, ToolCall("c2", "place_order", SHOP_DETAILS), "…"])
    assert "the delivery charge ৳60 paid online first, then ৳950 in cash on delivery" in (
        await world.tool_said(conv)
    )
    world.decider.script = [YES]
    await world.say(agent, conv, [ToolCall("c3", "place_order", SHOP_DETAILS), "Pay…"], "yes")
    order = await found(world, conv)
    assert order.status == "unpaid"  # held, not placed: the yes alone isn't enough
    assert f"https://heydesk.test/pay/{order.id}" in await world.tool_said(conv)

    payments = world.services.payments
    gateway.paid["short"] = Paid(order.id, 1000, "BDT", "B1")  # ৳10, not ৳60
    gateway.paid["elsewhere"] = Paid("another-order", 6000, "BDT", "B0")  # paid, but not this
    for forged_or_short in ("made-up", "short", "elsewhere"):
        await payments.settle(order.id, forged_or_short)
        assert (await found(world, conv)).status == "unpaid"

    gateway.paid["v1"] = Paid(order.id, 6000, "BDT", "B2")
    await payments.settle(order.id, "v1")
    await payments.settle(
        order.id, "v1"
    )  # the visitor's return and the gateway's notice: once is enough
    placed = await found(world, conv)
    # the advance confirms it: no call (D92)
    assert (placed.status, placed.paid, placed.payment_ref) == ("confirmed", 6000, "B2")
    said = (await world.conversation(conv)).messages
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
    agent, _ = await paying_first(world.services, "shop")
    sylhet = SHOP_DETAILS | {"area": "", "address": "Zindabazar, Sylhet"}  # the area from it
    first = [ToolCall("c1", "update_cart", SHOP_ORDER)]
    conv = await take(world, agent, first, ToolCall("c2", "place_order", sylhet))
    assert "pay_link" not in await world.tool_said(conv)
    [order] = await world.store(agent, conv).orders_here()
    assert (order.zone, order.status, order.total) == ("Outside Dhaka", "new", 107000)


async def test_the_restaurant_takes_the_delivery_charge_first_but_not_for_pickup(
    world: World,
) -> None:
    with pytest.raises(InvalidConfig, match="turn on delivery and set its charge"):
        await paying_first(world.services, "restaurant")  # pickup only: nothing to pay first
    agent, gateway = await paying_first(
        world.services, "restaurant", delivers=True, delivery_charge=6000
    )
    prompt = await RESTAURANT.prompt(agent.config, world.store(agent))
    assert "ask whether it's pickup or delivery" in prompt and "Delivery: ৳60" in prompt

    lines = {"lines": [{"code": "chk-biryani", "quantity": 2}, {"code": "borhani", "quantity": 1}]}
    order = [ToolCall("c1", "update_order", lines)]
    details = {"time": "7:30 pm", "name": "Kayes", "phone": "01712345678"}
    delivery = details | {"delivery": True, "address": "House 4, Road 2, Dhanmondi"}
    conv = await take(world, agent, order, ToolCall("c2", "place_order", delivery))
    read_back = "delivery ৳60 paid online now; total ৳1,090, the rest ৳1,030 paid on delivery"
    assert read_back in str((await world.conversation(conv)).messages)
    saved = await found(world, conv)
    assert (saved.status, saved.due, saved.total) == ("unpaid", 6000, 109000)

    gateway.paid["v1"] = Paid(saved.id, 6000, "BDT", "B1")
    await world.services.payments.settle(saved.id, "v1")
    assert (await found(world, conv)).status == "new"
    told = (await world.conversation(conv)).messages[-1]["content"]
    assert told == (
        "✅ Payment received: ৳60 for the delivery charge. Your order O-101 is placed. The rest, "
        "৳1,030, is paid on delivery."
    )

    pickup = await take(world, agent, order, ToolCall("c3", "place_order", details))
    assert "pay_link" not in await world.tool_said(pickup)  # pickup pays nothing first


async def test_the_clinic_request_waits_for_its_deposit_even_when_moved(world: World) -> None:
    with pytest.raises(InvalidConfig, match="set how much it is"):
        await paying_first(world.services, "clinic")
    agent, gateway = await paying_first(world.services, "clinic", deposit=50000)
    prompt = await CLINIC.prompt(agent.config, world.store(agent))
    assert "A deposit of ৳500 is paid online first" in prompt

    details = {"doctor": "Dr. Hasan", "time": "tomorrow at 7 pm", "name": "Kayes"}
    request = ToolCall("c", "request_appointment", details | {"phone": "01712345678"})
    conv = await take(world, agent, [], request)
    appointment = await found(world, conv)
    assert (appointment.status, appointment.due) == ("unpaid", 50000)

    move = {"code": "A-101", "name": "Kayes", "new_time": "tuesday at 8 pm"}
    world.decider.script = [YES]
    await world.say(agent, None, [ToolCall("m", "reschedule_appointment", move), "Moved."], "yes")
    assert (await found(world, conv)).status == "unpaid"  # moving it doesn't pay for it

    gateway.paid["v1"] = Paid(appointment.id, 50000, "BDT", "B1")
    await world.services.payments.settle(appointment.id, "v1")
    assert (await found(world, conv)).status == "new"
    told = (await world.conversation(conv)).messages[-1]["content"]
    assert told.startswith("✅ Payment received: ৳500 for the deposit. Your appointment request")


async def test_the_payment_link_goes_to_the_gateway_and_comes_back_placed(
    app: FastAPI, client: AsyncClient
) -> None:
    services = app.state.services
    agent, gateway = await paying_first(services, "shop")
    runtime = services.conversations.runtime
    runtime.decider.script = [YES]
    runtime.llm.script = [
        ToolCall("c1", "update_cart", SHOP_ORDER),
        ToolCall("c2", "place_order", SHOP_DETAILS),
        "Pay here…",
    ]
    await client.post(
        f"/api/chat/{agent.public_key}",
        json={"visitor_id": "test-visitor", "message": "a kurti, yes"},
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
    await services.agents.ensure_dev_agent("shop", SEEDS / "shop.yaml")
    await services.agents.update(DEV_WORKSPACE_ID, pay_first(services, "shop"))  # no store yet
    agent = await services.agents.by_key("dev-shop")
    assert "paid online first" not in await SHOP.prompt(agent.config, world.store(agent))
    assert (
        "Connect your SSLCommerz account to take payments first."
        in (await services.agents.get(DEV_WORKSPACE_ID)).missing
    )

    first = [ToolCall("c1", "update_cart", SHOP_ORDER)]
    conv = await take(world, agent, first, ToolCall("c2", "place_order", SHOP_DETAILS))
    assert "pay_link" not in await world.tool_said(conv)
    assert '"code": "O-101"' in await world.tool_said(conv)  # placed, cash on delivery


async def test_the_owner_connects_the_business_s_own_store(
    app: FastAPI, client: AsyncClient
) -> None:
    await app.state.services.agents.ensure_dev_agent("shop", SEEDS / "shop.yaml")
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
    assert stored and "the-api-password" not in stored  # encrypted in the database (D74)
    assert (await app.state.services.payments.account(DEV_WORKSPACE_ID)).password == body[
        "password"
    ]

    assert (await client.delete("/api/agent/sslcommerz")).status_code == 204
    assert (await client.get("/api/agent/sslcommerz")).status_code == 404
