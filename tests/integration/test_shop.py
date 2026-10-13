"""The shop desk: products with sizes, colours and stock; a cash-on-delivery order Python adds
up, saved only after a yes and taken out of stock; tracking and cancelling with the code and
the buyer's mobile; and the owner's products and orders routes."""

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select

from heydesk.desks.shop import SHOP
from heydesk.desks.shop.models import ShopOrder
from heydesk.desks.shop.tools import (
    FindOrderInput,
    PlaceOrderInput,
    UpdateCartInput,
    place_order,
    track_order,
    update_cart,
)
from heydesk.desks.shop.values import mobile
from heydesk.ports.llm import LLMUnavailable, ToolCall
from heydesk.services.agents import DEV_WORKSPACE_ID, LiveAgent
from tests.conftest import SEEDS, World, make_ctx

KURTI = {"code": "kurti", "quantity": 1, "size": "m", "colour": "black"}
PANJABIS = {"code": "panjabi-cotton", "quantity": 2, "size": "L", "colour": "White"}
DETAILS = {
    "area": "inside dhaka",
    "address": "House 12, Road 5, Mirpur 10, Dhaka, near the Shah Ali mosque",
    "name": "Kayes",
    "phone": "০১৭১২-৩৪৫৬৭৮",  # typed in Bangla digits
}
READ_BACK = "1 Cotton Kurti, ৳950 plus ৳60 delivery, ৳1,010 cash on delivery… Shall I place it?"
YES = {"read_back": 0.95, "said_yes": 0.95}
NO = {"read_back": 0.1, "said_yes": 0.1}


def cart(*lines: dict[str, object]) -> UpdateCartInput:
    return UpdateCartInput.model_validate({"lines": list(lines)})


async def orders(world: World) -> list[ShopOrder]:
    async with world.sessions() as s:
        return list(await s.scalars(select(ShopOrder)))


async def stock(world: World, agent: LiveAgent, code: str) -> int | None:
    return next(p.stock for p in await world.store(agent).products() if p.code == code)


async def buy(world: World, agent: LiveAgent, *lines: dict[str, object]) -> str:
    """A whole order over two turns, with a yes. Returns the order's code."""
    world.decider.script = [NO, YES]
    first = [
        ToolCall("c1", "update_cart", {"lines": list(lines)}),
        ToolCall("c2", "place_order", DETAILS),
    ]
    conv = await world.say(agent, None, [*first, READ_BACK], "a black kurti, size M…")
    await world.say(agent, conv, [ToolCall("c3", "place_order", DETAILS), "Done: O-101."], "yes")
    [saved] = await orders(world)
    return saved.code


def test_a_mobile_number_is_eleven_digits_however_it_is_typed() -> None:
    for said in ("01712345678", "+880 1712-345678", "8801712345678", "০১৭১২৩৪৫৬৭৮"):
        assert mobile(said) == "01712345678"
    for wrong in ("0171234567", "02 9123456", "01212345678"):  # too short, a landline, no 012
        with pytest.raises(ValueError, match="11 digits"):
            mobile(wrong)


async def test_the_prompt_has_the_products_and_delivery_and_the_note_has_the_cart(
    world: World,
) -> None:
    agent = await world.agent("shop")
    text = await SHOP.prompt(agent.config, world.store(agent))

    assert "never ask them to message for the price" in text
    assert "- Inside Dhaka: ৳60, usually 1–2 days" in text
    assert "Delivery is free on orders of ৳3,000 or more." in text
    assert "- Cotton Kurti [kurti] ৳950, sizes S/M/L/XL, colours Black/Mustard" in text
    assert "- Jamdani Saree [jamdani-saree] ৳6,500, colours Off-white/Red: Handwoven" in text
    assert text.count("SOLD OUT") == 1
    assert "# The shop" in text  # it has policies
    assert await SHOP.missing(agent.config, world.store(agent)) == []

    added = await update_cart(make_ctx(world.store(agent), agent=agent.config), cart(KURTI))
    assert SHOP.note(agent.config, {}) == ""
    assert "1 × Cotton Kurti (M, Black), subtotal ৳950" in SHOP.note(agent.config, added.state)


async def test_python_checks_sizes_colours_and_stock_and_adds_up_the_cart(world: World) -> None:
    agent = await world.agent("shop")
    ctx = make_ctx(world.store(agent), agent=agent.config)

    added = await update_cart(ctx, cart(PANJABIS, KURTI, PANJABIS | {"quantity": 1}))
    assert added.ok and added.data["subtotal"] == "৳6,500"  # 3 panjabis + a kurti
    assert added.data["order"][0] == "3 × Cotton Panjabi (L, White) = ৳5,550"

    no_size = await update_cart(ctx, cart(KURTI | {"size": ""}))
    assert "Ask which size: S, M, L, XL" in no_size.data["error"]
    wrong_colour = await update_cart(ctx, cart(KURTI | {"colour": "pink"}))
    assert (
        "'pink' isn't one of them. Ask which colour: Black, Mustard" in wrong_colour.data["error"]
    )
    too_many = await update_cart(
        ctx, cart({"code": "panjabi-silk", "quantity": 3, "size": "M", "colour": "Gold"})
    )
    assert "Silk Panjabi: only 2 left" in too_many.data["error"]
    sold_out = await update_cart(
        ctx, cart({"code": "jamdani-saree", "quantity": 1, "colour": "Red"})
    )
    assert "Jamdani Saree: sold out" in sold_out.data["error"]
    unknown = await update_cart(ctx, cart({"code": "lungi", "quantity": 1}))
    assert "Not in the shop: lungi" in unknown.data["error"]


async def test_place_order_gives_the_delivery_charge_then_asks_for_what_is_missing(
    world: World,
) -> None:
    agent = await world.agent("shop")
    store = world.store(agent)
    kurti = (await update_cart(make_ctx(store, agent=agent.config), cart(KURTI))).state

    ctx = make_ctx(store, state=kurti, agent=agent.config)
    no_area = await place_order(ctx, PlaceOrderInput())
    assert (
        no_area.data["next"] == "Ask for the area, one question at a time."
        and "total" not in no_area.data
    )
    wrong_area = await place_order(ctx, PlaceOrderInput(area="Chittagong"))
    assert "Inside Dhaka or Dhaka suburbs or Outside Dhaka" in wrong_area.data["problems"]["area"]

    just_the_area = await place_order(ctx, PlaceOrderInput(area="outside"))
    assert (just_the_area.data["delivery"], just_the_area.data["total"]) == ("৳120", "৳1,070")
    assert just_the_area.data["still_needed"] == ["address", "name", "phone"]

    all_there = await place_order(ctx, PlaceOrderInput.model_validate(DETAILS))
    assert all_there.data["read_back"] == (
        "1 × Cotton Kurti (M, Black), subtotal ৳950; delivery Inside Dhaka ৳60; total ৳1,010, "
        "paid in cash on delivery; to House 12, Road 5, Mirpur 10, Dhaka, near the Shah Ali "
        "mosque; name Kayes; mobile 01712345678"
    )
    big = (await update_cart(make_ctx(store, agent=agent.config), cart(PANJABIS))).state
    free = await place_order(
        make_ctx(store, state=big, agent=agent.config), PlaceOrderInput(area="inside dhaka")
    )
    assert (free.data["delivery"], free.data["total"]) == ("free", "৳3,700")  # over ৳3,000
    assert await orders(world) == []


async def test_an_order_is_saved_only_at_the_yes_and_taken_out_of_stock(world: World) -> None:
    agent = await world.agent("shop")
    world.decider.script = [NO, {"read_back": 0.95, "said_yes": 0.3}]  # then "hmm, maybe"
    first = [
        ToolCall(
            "c1",
            "update_cart",
            {"lines": [KURTI | {"code": "panjabi-silk", "size": "M", "colour": "Gold"}]},
        )
    ]
    conv = await world.say(agent, None, [*first, ToolCall("c2", "place_order", DETAILS), READ_BACK])
    await world.say(agent, conv, [ToolCall("c3", "place_order", DETAILS), READ_BACK], "hmm, maybe")
    assert await orders(world) == []

    world.decider.script = [YES]
    await world.say(agent, conv, [ToolCall("c4", "place_order", DETAILS), "Done."], "yes")
    [saved] = await orders(world)
    assert (saved.code, saved.conversation_id, saved.status) == ("O-101", conv, "new")
    assert (saved.subtotal, saved.delivery_charge, saved.total) == (320000, 0, 320000)  # free
    assert (saved.zone, saved.phone) == ("Inside Dhaka", "01712345678")
    assert await stock(world, agent, "panjabi-silk") == 1  # was 2
    assert (await world.conversation(conv)).state["cart"] == {}


async def test_an_order_is_tracked_and_cancelled_with_its_code_and_mobile(world: World) -> None:
    agent = await world.agent("shop")
    code = await buy(world, agent, PANJABIS)
    assert await stock(world, agent, "panjabi-cotton") == 10

    track = ToolCall("t", "track_order", {"code": "o 101", "phone": "+8801712345678"})
    conv = await world.say(agent, None, [track, "It's received."])
    assert "the shop will call to confirm it" in await world.tool_said(conv)
    stranger = ToolCall("t", "track_order", {"code": code, "phone": "01811111111"})
    conv = await world.say(agent, None, [stranger, "Not found."])
    assert "No order matches that code and mobile number" in await world.tool_said(conv)

    world.decider.script = [NO, YES]
    cancel = ToolCall("x", "cancel_order", {"code": code, "phone": "01712345678"})
    conv = await world.say(agent, None, [cancel, "Cancel O-101: 2 Cotton Panjabi, ৳3,700?"])
    assert (
        "Cancel order O-101: 2 × Cotton Panjabi (L, White), total ৳3,700"
        in await world.tool_said(conv)
    )
    await world.say(agent, conv, [cancel, "Cancelled."], "yes")
    [cancelled] = await orders(world)
    assert cancelled.status == "cancelled"
    assert await stock(world, agent, "panjabi-cotton") == 12  # back in stock


async def test_the_owner_moves_orders_along_and_the_buyer_sees_the_courier(
    app: FastAPI, client: AsyncClient
) -> None:
    services = app.state.services
    agent = await services.agents.ensure_dev_agent("shop", SEEDS / "shop.yaml")
    assert (await client.get("/api/menu")).status_code == 404  # a shop has no menu
    assert len((await client.get("/api/products")).json()) == 6

    runtime = services.conversations.runtime
    runtime.decider.script = [NO, YES]
    runtime.llm.script = [
        ToolCall("c1", "update_cart", {"lines": [KURTI]}),
        ToolCall("c2", "place_order", DETAILS),
        READ_BACK,
        ToolCall("c3", "place_order", DETAILS),
        "Done: O-101.",
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
    assert (order["code"], order["total"], order["address"][:8]) == ("O-101", 101000, "House 12")
    assert (await client.get("/api/shop/orders", params={"q": "O-101"})).json()[0]["id"] == order[
        "id"
    ]

    url = f"/api/shop/orders/{order['id']}"
    early = await client.patch(url, json={"status": "shipped"})
    assert early.status_code == 409  # call the buyer to confirm first
    await client.patch(url, json={"status": "confirmed"})
    shipped = await client.patch(url, json={"status": "shipped", "tracking": "Steadfast 12345678"})
    assert (shipped.json()["status"], shipped.json()["tracking"]) == (
        "shipped",
        "Steadfast 12345678",
    )

    store = await services.agents.store(DEV_WORKSPACE_ID, "shop")
    found = FindOrderInput(code="O-101", phone="01712345678")
    tracked = await track_order(make_ctx(store, agent=agent.config), found)
    assert (tracked.data["courier"], tracked.data["usually_takes"]) == (
        "Steadfast 12345678",
        "1–2 days",
    )


async def test_products_come_from_a_csv(app: FastAPI, client: AsyncClient) -> None:
    await app.state.services.agents.ensure_dev_agent("shop", SEEDS / "shop.yaml")
    csv = "Name,Price (৳),Sizes,Colors,Stock\nCotton Lungi,450,,Blue; Green,20\nTee,350,S; M,,\n"
    await client.post("/api/products/import", json={"csv": csv})
    lungi, tee = (await client.get("/api/products")).json()
    assert (lungi["code"], lungi["price"], lungi["colours"], lungi["stock"]) == (
        "cotton-lungi",
        45000,
        ["Blue", "Green"],
        20,
    )
    assert (tee["sizes"], tee["stock"]) == (["S", "M"], None)  # stock not counted


SHEET = 'Item Title,MRP,Qty,Supplier\nCotton Panjabi,"৳1,850",12,Rahim & Co\nTee,350,,\n'


async def test_their_columns_are_matched_to_ours_and_imported(
    app: FastAPI, client: AsyncClient
) -> None:
    await app.state.services.agents.ensure_dev_agent("shop", SEEDS / "shop.yaml")
    app.state.services.imports.llm.script.append(
        'Here: {"name": "Item Title", "price": "MRP", "stock": "Qty", '
        '"colours": "Nope", "owner": "Supplier"}'  # a column that isn't there; not our field
    )
    suggested = (await client.post("/api/products/import/suggest", json={"csv": SHEET})).json()
    assert suggested["mapping"] == {"name": "Item Title", "price": "MRP", "stock": "Qty"}
    assert suggested["by_ai"] and suggested["headers"][0] == "Item Title"
    assert suggested["samples"][0]["MRP"] == "৳1,850"

    body = {"csv": SHEET, "mapping": suggested["mapping"]}
    await client.post("/api/products/import", json=body)
    panjabi, tee = (await client.get("/api/products")).json()
    assert (panjabi["name"], panjabi["price"], panjabi["stock"]) == ("Cotton Panjabi", 185000, 12)
    assert (tee["price"], tee["stock"]) == (35000, None)


async def test_without_the_model_columns_match_by_name(app: FastAPI, client: AsyncClient) -> None:
    await app.state.services.agents.ensure_dev_agent("shop", SEEDS / "shop.yaml")
    sheet = "Name,Price (৳),Supplier\nTee,350,Rahim\n"
    for failure in [LLMUnavailable("down"), '{"Name": "Tee"}']:  # down, or nothing usable
        app.state.services.imports.llm.script.append(failure)
        reply = await client.post("/api/products/import/suggest", json={"csv": sheet})
        suggested = reply.json()
        assert suggested["mapping"] == {"name": "Name", "price": "Price (৳)"}
        assert not suggested["by_ai"]

    no_price = {"csv": sheet, "mapping": {"name": "Name"}}
    refused = await client.post("/api/products/import", json=no_price)
    assert refused.status_code == 422 and "price" in refused.text
