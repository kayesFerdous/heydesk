"""The shop desk: products with sizes, colours and stock; a cash-on-delivery order Python adds
up, saved only after a yes and taken out of stock, and confirmed by the shop's call or at once
(D92); tracking it (the one from this chat, by the buyer's mobile, or with its code), changing
its items or details and cancelling it; an exchange request once it's delivered; the owner's products and orders routes; and photos: the owner's, a visitor's sent to
find a product, and the ones the agent shows (D57)."""

import base64
import io
import json
from datetime import timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from PIL import Image
from sqlalchemy import select

from heydesk.desks import csv_import
from heydesk.desks.shop import SHOP
from heydesk.desks.shop.models import ShopOrder
from heydesk.desks.shop.tools import (
    ChangeOrderInput,
    ExchangeInput,
    FindOrderInput,
    FindProductInput,
    PlaceOrderInput,
    ShowPhotosInput,
    UpdateCartInput,
    change_order,
    find_product,
    place_order,
    request_exchange,
    show_photos,
    track_order,
    update_cart,
)
from heydesk.desks.shop.values import (
    LONG_LIST,
    PRODUCT_COLUMNS,
    STATUS_WORDS,
    Product,
    added,
    mobile,
    products_from_csv,
    stock_cell,
)
from heydesk.ports.llm import ToolCall
from heydesk.ports.media import ImageUnreachable
from heydesk.runtime.tool import ToolContext
from heydesk.services.agents import DEV_WORKSPACE_ID, LiveAgent
from heydesk.services.photos import to_jpeg
from tests.conftest import DEV_KEY, FIXED_NOW, SEEDS, World, make_ctx, new_business, said_yes

KURTI = {"code": "kurti", "quantity": 1, "size": "m", "colour": "black"}
PANJABIS = {"code": "panjabi-cotton", "quantity": 2, "size": "L", "colour": "White"}
DETAILS = {
    "district": "Dhaka",
    "address": "House 12, Road 5, Mirpur 10, Dhaka, near the Shah Ali mosque",
    "name": "Kayes",
    "phone": "০১৭১২-৩৪৫৬৭৮",  # typed in Bangla digits
}
READ_BACK = "1 Cotton Kurti, ৳950 plus ৳60 delivery, ৳1,010 cash on delivery… Shall I place it?"


def cart(*lines: dict[str, object]) -> UpdateCartInput:
    return UpdateCartInput.model_validate({"lines": list(lines)})


async def orders(world: World) -> list[ShopOrder]:
    async with world.sessions() as s:
        return list(await s.scalars(select(ShopOrder)))


async def stock(
    world: World, agent: LiveAgent, code: str, size: str = "", colour: str = ""
) -> int | None:
    """What's left of a product, or of its variant of that size and colour (D56)."""
    product = next(p for p in await world.store(agent).products() if p.code == code)
    counter = product.counter(size, colour)
    return None if counter is None else product.count(counter)


async def buy(world: World, agent: LiveAgent, *lines: dict[str, object]) -> str:
    """A whole order over two turns, with a yes. Returns the order's code."""
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
    assert "Never switch to Bangla letters or to English." in text
    assert "- Inside Dhaka: ৳60, usually 1–2 days (Dhaka)" in text
    assert "- Outside Dhaka: ৳120, usually 3–5 days (everywhere else)" in text
    assert "Delivery is free on orders of ৳3,000 or more." in text
    assert "- Cotton Kurti [kurti] ৳950, sizes S/M/L/XL, colours Black/Mustard" in text
    assert "- Jamdani Saree [jamdani-saree] ৳6,500, colours Off-white/Red: Handwoven" in text
    assert text.count("SOLD OUT") == 1
    assert (
        "- Silk Panjabi [panjabi-silk] ৳3,200, sizes M/L/XL, colours Maroon/Gold (only 2 left)"
        in text
    )
    assert "- Lawn Three-Piece [three-piece] ৳2,450 (was ৳2,900, on sale)" in text
    assert "# Size guide\nPanjabi, in inches" in text
    no_guide = agent.config.model_copy(update={"size_guide": ""})
    assert "# Size guide" not in await SHOP.prompt(no_guide, world.store(agent))
    assert "# The shop" in text  # it has policies
    assert await SHOP.missing(agent.config, world.store(agent)) == []

    added = await update_cart(make_ctx(world.store(agent), agent=agent.config), cart(KURTI))
    assert SHOP.note(agent.config, {}).startswith("\n# Language\nWrite your reply")
    assert "# The order so far" not in SHOP.note(agent.config, {})
    assert "1 × Cotton Kurti (M, Black), subtotal ৳950" in SHOP.note(agent.config, added.state)


async def test_python_checks_sizes_colours_and_stock_and_adds_up_the_cart(world: World) -> None:
    agent = await world.agent("shop")
    ctx = make_ctx(world.store(agent), agent=agent.config)

    added = await update_cart(ctx, cart(PANJABIS, KURTI, PANJABIS | {"quantity": 1}))
    assert added.ok and added.data["subtotal"] == "৳6,500"  # 3 panjabis + a kurti
    assert "free_delivery_if_you_add" not in added.data  # over ৳3,000 already
    assert added.data["delivery"] == "free"  # said, so the model doesn't make up a nudge
    just_a_kurti = await update_cart(ctx, cart(KURTI))
    assert just_a_kurti.data["free_delivery_if_you_add"] == "৳2,050"  # ৳3,000 - ৳950
    assert "delivery" not in just_a_kurti.data
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


async def test_the_buttons_for_a_size_or_colour_are_what_is_in_stock(world: World) -> None:
    """Python picks the buttons (D99): L in Navy is sold out, so neither offers it."""
    agent = await world.agent("shop")
    ctx = make_ctx(world.store(agent), agent=agent.config)
    panjabi = {"code": "panjabi-cotton", "quantity": 1}

    navy = await update_cart(ctx, cart(panjabi | {"colour": "Navy"}))
    assert navy.choices == ["M", "XL", "XXL"]
    large = await update_cart(ctx, cart(panjabi | {"size": "L"}))
    assert large.choices == ["White", "Cream"]
    assert (await update_cart(ctx, cart(KURTI | {"size": ""}))).choices == ["S", "M", "L", "XL"]
    assert (await update_cart(ctx, cart(KURTI))).choices is None  # nothing to ask


async def test_place_order_gives_the_delivery_charge_then_asks_for_what_is_missing(
    world: World,
) -> None:
    agent = await world.agent("shop")
    store = world.store(agent)
    kurti = (await update_cart(make_ctx(store, agent=agent.config), cart(KURTI))).state

    ctx = make_ctx(store, state=kurti, agent=agent.config)
    nothing = await place_order(ctx, PlaceOrderInput())  # a chat asks for it all at once
    assert (
        nothing.data["next"] == "Ask for all of these in one message: address, name, phone."
        and "total" not in nothing.data
    )
    on_a_call = await place_order(
        make_ctx(store, state=kurti, agent=agent.config, channel="voice"), PlaceOrderInput()
    )
    assert on_a_call.data["next"] == "Ask for the address, one question at a time."
    no_district = await place_order(ctx, PlaceOrderInput(address="Road 3, Uttara"))
    assert no_district.data["still_needed"] == ["district", "name", "phone"]
    not_a_district = await place_order(ctx, PlaceOrderInput(district="Teknaf"))  # an upazila
    assert "isn't a district" in not_a_district.data["problems"]["district"]

    just_the_district = await place_order(ctx, PlaceOrderInput(district="Feni"))  # listed nowhere
    assert (just_the_district.data["delivery"], just_the_district.data["total"]) == (
        "৳120",
        "৳1,070",
    )
    assert just_the_district.data["still_needed"] == ["address", "name", "phone"]
    for address, district, charge in [
        ("Road 3, Uttara", "Dhaka", "৳60"),
        ("Bank Colony, Savar", "Dhaka", "৳60"),  # Savar is in Dhaka district
        ("Tongi, Gazipur", "gazipur district", "৳100"),
        ("Agrabad", "Chittagong", "৳120"),  # the old spelling, and everywhere else
    ]:
        found = await place_order(ctx, PlaceOrderInput(address=address, district=district))
        assert found.data["delivery"] == charge, address

    all_there = await place_order(ctx, PlaceOrderInput.model_validate(DETAILS))
    assert all_there.data["read_back"] == (
        "1 × Cotton Kurti (M, Black), subtotal ৳950; delivery Inside Dhaka ৳60; total ৳1,010, "
        "paid in cash on delivery; to House 12, Road 5, Mirpur 10, Dhaka, near the Shah Ali "
        "mosque; name Kayes; mobile 01712345678"
    )
    big = (await update_cart(make_ctx(store, agent=agent.config), cart(PANJABIS))).state
    free = await place_order(
        make_ctx(store, state=big, agent=agent.config), PlaceOrderInput(district="Dhaka")
    )
    assert (free.data["delivery"], free.data["total"]) == ("free", "৳3,700")  # over ৳3,000
    assert await orders(world) == []


async def test_an_order_is_saved_only_at_the_yes_and_taken_out_of_stock(world: World) -> None:
    agent = await world.agent("shop")
    first = [
        ToolCall(
            "c1",
            "update_cart",
            {"lines": [KURTI | {"code": "panjabi-silk", "size": "M", "colour": "Gold"}]},
        )
    ]
    conv = await world.say(agent, None, [*first, ToolCall("c2", "place_order", DETAILS), READ_BACK])
    await world.say(agent, conv, [READ_BACK], "hmm, maybe")  # not a yes: the model asks again
    assert await orders(world) == []

    await world.say(agent, conv, [ToolCall("c4", "place_order", DETAILS), "Done."], "yes")
    [saved] = await orders(world)
    assert (saved.code, saved.conversation_id, saved.status) == ("O-101", conv, "new")
    assert (saved.subtotal, saved.delivery_charge, saved.total) == (320000, 0, 320000)  # free
    assert (saved.zone, saved.phone) == ("Inside Dhaka", "01712345678")
    assert await stock(world, agent, "panjabi-silk") == 1  # was 2
    assert (await world.conversation(conv)).state["cart"] == {}


async def test_the_owner_hears_about_past_returns_and_what_is_running_low(world: World) -> None:
    agent = await world.agent("shop")
    store = world.store(agent)
    await buy(world, agent, KURTI)
    [first] = await orders(world)
    await store.move_order(first.id, "cancelled")

    silk = {"code": "panjabi-silk", "quantity": 1, "size": "M", "colour": "Gold"}
    state = (await update_cart(make_ctx(store, agent=agent.config), cart(silk))).state
    ctx = make_ctx(store, state=state, agent=agent.config)
    placed = await said_yes(place_order, ctx, PlaceOrderInput.model_validate(DETAILS))

    assert placed.ok and placed.alert
    assert "🛍️ New order O-102" in placed.alert
    assert "⚠️ This mobile before: 1 cancelled" in placed.alert
    assert "📦 Running low: Silk Panjabi: 1 left" in placed.alert
    assert "the shop will call 01712345678 to confirm it" in placed.data["next"]
    assert (
        "pay ৳3,200 to the delivery man, who calls before he comes. Delivery usually takes 1–2 "
        "days." in placed.data["next"]
    )
    assert "This mobile" not in str(placed.data)  # for the owner, never the visitor


async def test_an_order_is_tracked_and_cancelled_with_its_code_and_mobile(world: World) -> None:
    agent = await world.agent("shop")
    code = await buy(world, agent, PANJABIS)
    assert await stock(world, agent, "panjabi-cotton", "L", "White") == 1  # was 3

    track = ToolCall("t", "track_order", {"code": "o 101", "phone": "+8801712345678"})
    conv = await world.say(agent, None, [track, "It's received."])
    assert "the shop will call to confirm it" in await world.tool_said(conv)
    stranger = ToolCall("t", "track_order", {"code": code, "phone": "01811111111"})
    conv = await world.say(agent, None, [stranger, "Not found."])
    assert "No order matches that code and mobile number" in await world.tool_said(conv)

    cancel = ToolCall("x", "cancel_order", {"code": code, "phone": "01712345678"})
    conv = await world.say(agent, None, [cancel, "Cancel O-101: 2 Cotton Panjabi, ৳3,700?"])
    assert (
        "Cancel order O-101: 2 × Cotton Panjabi (L, White), total ৳3,700"
        in await world.tool_said(conv)
    )
    await world.say(agent, conv, [cancel, "Cancelled."], "yes")
    [cancelled] = await orders(world)
    assert cancelled.status == "cancelled"
    assert await stock(world, agent, "panjabi-cotton", "L", "White") == 3  # back in stock


async def test_the_owner_moves_orders_along_and_the_buyer_sees_it(
    app: FastAPI, client: AsyncClient
) -> None:
    services = app.state.services
    agent = await services.agents.ensure_dev_agent("shop", SEEDS / "shop.yaml")
    assert (await client.get("/api/menu")).status_code == 404  # a shop has no menu
    assert len((await client.get("/api/products")).json()) == 6

    runtime = services.conversations.runtime
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
    shipped = await client.patch(url, json={"status": "shipped"})
    assert shipped.json()["status"] == "shipped"
    team = await client.get(f"/api/chat/{agent.public_key}/{conv_id}/team")
    assert team.json()["replies"] == [  # the buyer hears it in their chat (D66)
        "✅ Your order O-101 is confirmed and will be sent out.",
        "📦 Your order O-101 is with the courier. The delivery man calls before he comes.",
    ]

    store = await services.agents.store(DEV_WORKSPACE_ID, "shop")
    found = FindOrderInput(code="O-101", phone="01712345678")
    tracked = await track_order(make_ctx(store, agent=agent.config), found)
    assert (tracked.data["status"], tracked.data["usually_takes"]) == (
        STATUS_WORDS["shipped"],
        "1–2 days",
    )


async def test_products_come_from_a_csv(app: FastAPI, client: AsyncClient) -> None:
    await app.state.services.agents.ensure_dev_agent("shop", SEEDS / "shop.yaml")
    seeded = [p["code"] for p in (await client.get("/api/products")).json()]
    csv = (
        "Name,Price (৳),Was price,Sizes,Colors,Stock\n"
        "Cotton Lungi,450,600,,Blue; Green,20\nTee,350,,S; M,,\n"
    )
    first = (await client.post("/api/products/import", json={"csv": csv})).json()
    assert (first["count"], first["added"]) == (2, 2)
    listed = {p["code"]: p for p in (await client.get("/api/products")).json()}
    assert list(listed) == [*seeded, "cotton-lungi", "tee"]  # added at the end, none deleted
    lungi, tee = listed["cotton-lungi"], listed["tee"]
    assert (lungi["code"], lungi["price"], lungi["colours"], lungi["stock"]) == (
        "cotton-lungi",
        45000,
        ["Blue", "Green"],
        20,
    )
    assert lungi["was_price"] == 60000  # on sale
    assert (tee["sizes"], tee["stock"], tee["was_price"]) == (["S", "M"], None, None)

    per_size = "Name,Price,Sizes,Stock\nTee,350,M; L,M: 2; L: 0\n"
    second = (await client.post("/api/products/import", json={"csv": per_size})).json()
    assert second["added"] == 0  # the same code: updated
    listed = {p["code"]: p for p in (await client.get("/api/products")).json()}
    assert list(listed) == [*seeded, "cotton-lungi", "tee"]
    tee_now = listed["tee"]
    assert (tee_now["stock"], tee_now["variants"]) == (
        None,
        [{"size": "M", "colour": "", "stock": 2}, {"size": "L", "colour": "", "stock": 0}],
    )
    no_such_size = [{**tee_now, "variants": [{"size": "XXL", "stock": 1}]}]
    refused = await client.put("/api/products", json=no_such_size)
    assert refused.status_code == 422 and "isn't one of its sizes" in refused.text


async def test_a_second_source_updates_the_same_product_by_name(
    app: FastAPI, client: AsyncClient
) -> None:
    """Another source's code for a product we have: the same name is the same product (D120)."""
    await app.state.services.agents.ensure_dev_agent("shop", SEEDS / "shop.yaml")
    await client.post("/api/products/import", json={"csv": "Name,Price\nCotton Lungi,450\n"})
    count = len((await client.get("/api/products")).json())
    mapping = {"code": "SKU", "name": "Title", "price": "Price"}
    other = "SKU,Title,Price\nLG-7, cotton lungi ,480\nGM-1,Gamchha,150\n"
    body = {"csv": other, "mapping": mapping}
    assert (await client.post("/api/products/import", json=body)).json()["added"] == 1
    listed = {p["code"]: p for p in (await client.get("/api/products")).json()}
    assert len(listed) == count + 1  # Gamchha
    assert "cotton-lungi" not in listed and listed["LG-7"]["price"] == 48000


SHEET = 'Item Title,MRP,Qty,Supplier\nCotton Panjabi,"৳1,850",12,Rahim & Co\nTee,350,,\n'


async def test_their_columns_are_matched_to_ours_and_imported(
    app: FastAPI, client: AsyncClient
) -> None:
    """Python matches the columns by their names, with no model (D115); the owner's checked
    mapping is what the import takes."""
    await app.state.services.agents.ensure_dev_agent("shop", SEEDS / "shop.yaml")
    suggested = (await client.post("/api/products/import/suggest", json={"csv": SHEET})).json()
    assert suggested["mapping"] == {"name": "Item Title", "price": "MRP", "stock": "Qty"}
    assert suggested["headers"][0] == "Item Title"
    assert suggested["samples"][0]["MRP"] == "৳1,850"

    body = {"csv": SHEET, "mapping": suggested["mapping"]}
    await client.post("/api/products/import", json=body)
    named = {p["name"]: p for p in (await client.get("/api/products")).json()}
    panjabi, tee = named["Cotton Panjabi"], named["Tee"]
    assert (panjabi["name"], panjabi["price"], panjabi["stock"]) == ("Cotton Panjabi", 185000, 12)
    assert (tee["price"], tee["stock"]) == (35000, None)

    no_price = {"csv": SHEET, "mapping": {"name": "Item Title"}}  # the owner took price off
    refused = await client.post("/api/products/import", json=no_price)
    assert refused.status_code == 422 and "price" in refused.text


def test_columns_match_by_other_names_bangla_and_misspellings() -> None:
    headers = ["পণ্যের নাম", "দাম (৳)", "Regular Price", "Colors", "Catagory", "SKU", "Notes"]
    assert csv_import.match(headers, PRODUCT_COLUMNS) == {
        "name": "পণ্যের নাম",
        "price": "দাম (৳)",
        "was_price": "Regular Price",
        "colours": "Colors",
        "category": "Catagory",  # misspelt
        "code": "SKU",
    }  # "Notes" fits nothing: left for the owner
    assert csv_import.match(["Product Name", "Category Name"], PRODUCT_COLUMNS) == {
        "name": "Product Name",
        "category": "Category Name",
    }


async def test_the_order_from_this_chat_needs_no_code(world: World) -> None:
    agent = await world.agent("shop")
    await buy(world, agent, KURTI)
    [order] = await orders(world)

    no_code = ToolCall("t", "track_order", {})
    await world.say(agent, order.conversation_id, [no_code, "It's received."], "amar order kothay?")
    assert "the shop will call to confirm it" in await world.tool_said(str(order.conversation_id))

    elsewhere = await world.say(agent, None, [no_code, "Your number?"], "amar order kothay?")
    assert "Ask for the mobile number" in await world.tool_said(elsewhere)  # another chat
    by_phone = ToolCall("t", "track_order", {"phone": "01712-345678"})  # no code: buyers forget
    await world.say(agent, elsewhere, [by_phone, "It's received."], "01712-345678")
    assert '"order": "O-101"' in await world.tool_said(elsewhere)
    stranger = ToolCall("t", "track_order", {"phone": "01811111111"})
    await world.say(agent, elsewhere, [stranger, "Not found."], "01811111111")
    assert "No order was placed with that mobile" in await world.tool_said(elsewhere)

    cancel = ToolCall("x", "cancel_order", {"phone": "01712345678"})  # changing needs the code
    await world.say(agent, elsewhere, [cancel, "Which code?"], "cancel it")
    assert "Ask for the order code" in await world.tool_said(elsewhere)


async def test_a_shop_that_doesnt_call_confirms_an_order_when_its_placed(world: World) -> None:
    agent = await world.agent("shop")
    no_call = agent.config.model_copy(update={"call_to_confirm": False})
    assert "one is confirmed as soon as it's placed: the shop doesn't call" in await SHOP.prompt(
        no_call, world.store(agent)
    )
    state = (await update_cart(make_ctx(world.store(agent), agent=no_call), cart(KURTI))).state
    ctx = make_ctx(world.store(agent), state=state, agent=no_call)
    placed = await said_yes(place_order, ctx, PlaceOrderInput.model_validate(DETAILS))
    assert "that it's confirmed and will be sent out" in placed.data["next"]
    assert "will call" not in placed.data["next"]
    [order] = await orders(world)
    assert order.status == "confirmed"  # ready for the owner to send

    calls = agent.config.calls.model_copy(update={"enabled": True})
    with pytest.raises(ValueError, match="turn on call_to_confirm"):
        type(no_call).model_validate(no_call.model_dump() | {"calls": calls.model_dump()})


async def test_an_order_changes_before_the_courier_has_it_and_only_after_a_yes(
    world: World,
) -> None:
    agent = await world.agent("shop")
    await buy(world, agent, KURTI)  # ৳950 inside Dhaka: ৳60 delivery, ৳1,010
    [order] = await orders(world)
    store = world.store(agent, order.conversation_id)

    def ctx(heard: str = "") -> ToolContext[Any]:
        return make_ctx(store, agent=agent.config, heard=heard)

    no_district = await change_order(ctx(), ChangeOrderInput(address="Zindabazar, Sylhet"))
    assert "district too" in no_district.data["error"]
    sylhet = ChangeOrderInput(district="Sylhet", address="Zindabazar, Sylhet")
    asked = await change_order(ctx(), sylhet)
    assert asked.data["read_back"] == (
        "Change order O-101: address: House 12, Road 5, Mirpur 10, Dhaka, near the Shah Ali "
        "mosque → Zindabazar, Sylhet; delivery area: Inside Dhaka → Outside Dhaka; delivery: "
        "৳60 → ৳120; total: ৳1,010 → ৳1,070"
    )
    [unchanged] = await orders(world)
    assert unchanged.zone == "Inside Dhaka"  # no yes, no change

    done = await change_order(ctx(asked.data["read_back"]), sylhet)
    assert done.ok and done.alert and "✏️ Order O-101 changed" in done.alert
    [changed] = await orders(world)
    assert (changed.zone, changed.delivery_charge, changed.total) == (
        "Outside Dhaka",
        12000,
        107000,
    )

    nothing = await change_order(ctx(), ChangeOrderInput())
    assert "Ask what they want to change" in nothing.data["error"]

    two = ChangeOrderInput.model_validate({"items": [KURTI | {"quantity": 2}]})  # D92
    asked = await change_order(ctx(), two)
    assert asked.data["read_back"] == (
        "Change order O-101: items: 1 × Cotton Kurti (M, Black) → 2 × Cotton Kurti (M, Black); "
        "total: ৳1,070 → ৳2,020"
    )
    assert (await change_order(ctx(asked.data["read_back"]), two)).ok
    [more] = await orders(world)
    assert (more.subtotal, more.total, more.lines[0]["quantity"]) == (190000, 202000, 2)
    sold_out = {"code": "jamdani-saree", "quantity": 1, "colour": "Red"}
    swap = ChangeOrderInput.model_validate({"items": [sold_out]})
    refused = await change_order(ctx(), swap)
    assert "Not enough in stock" in refused.data["error"]
    assert (await orders(world))[0].lines == more.lines  # nothing changed
    await store.move_order(changed.id, "confirmed")
    await store.move_order(changed.id, "shipped")
    late = await change_order(ctx(), ChangeOrderInput(note="ring twice"))
    assert "can't be changed now" in late.data["error"]


async def test_the_area_stays_once_its_charge_was_paid_online(world: World) -> None:
    agent = await world.agent("shop")
    await buy(world, agent, KURTI)
    [order] = await orders(world)
    async with world.sessions() as s, s.begin():  # as if the delivery charge was paid first
        (await s.get(ShopOrder, order.id)).paid = 6000  # type: ignore[union-attr]

    ctx = make_ctx(world.store(agent, order.conversation_id), agent=agent.config)
    refused = await change_order(ctx, ChangeOrderInput(district="Sylhet", address="Zindabazar"))
    assert "was paid online, so the area can't change" in refused.data["error"]


async def test_an_exchange_is_asked_for_within_the_days_after_delivery(world: World) -> None:
    agent = await world.agent("shop")  # the seed takes exchanges for 3 days
    await buy(world, agent, PANJABIS)
    [order] = await orders(world)
    store = world.store(agent, order.conversation_id)
    swap = ExchangeInput(product="panjabi-cotton", size="xl", reason="too tight")

    early = await request_exchange(make_ctx(store, agent=agent.config), swap)
    assert "only for a delivered order" in early.data["error"]
    for status in ("confirmed", "shipped", "delivered"):
        await store.move_order(order.id, status)

    two_days_on = make_ctx(store, agent=agent.config, now=FIXED_NOW + timedelta(days=2))
    no_such_size = await request_exchange(
        two_days_on, ExchangeInput(product="panjabi-cotton", size="3XL")
    )
    assert "'3XL' isn't one of them. Ask which size: M, L, XL, XXL" in no_such_size.data["error"]
    same = await request_exchange(two_days_on, ExchangeInput(product="panjabi-cotton", size="L"))
    assert "That's the size and colour they have" in same.data["error"]
    not_ordered = await request_exchange(two_days_on, ExchangeInput(product="kurti", size="M"))
    assert "The order has: 2 × Cotton Panjabi (L, White)" in not_ordered.data["error"]

    asked = await said_yes(request_exchange, two_days_on, swap)
    assert asked.ok and asked.data["code"] == "M-101"
    assert asked.alert and (
        "Exchange for order O-101: 1 × Cotton Panjabi (L, White) for 1 × Cotton Panjabi "
        "(XL, White); why: too tight"
    ) in (asked.alert)

    too_late = make_ctx(store, agent=agent.config, now=FIXED_NOW + timedelta(days=5))
    refused = await request_exchange(too_late, swap)
    assert "delivered 5 days ago; exchanges are within 3 days" in refused.data["error"]


async def test_stock_is_counted_per_size_and_colour(world: World) -> None:
    agent = await world.agent("shop")  # Cotton Panjabi: M any colour 4, L/White 3, L/Navy 0,
    store = world.store(agent)  # XL/White 2; the rest isn't counted
    ctx = make_ctx(store, agent=agent.config)
    text = await SHOP.prompt(agent.config, store)
    assert (
        "Fits true to size. (sold out: L/Navy) (only 3 left: L/White) (only 2 left: XL/White)"
        in text
    )

    navy = PANJABIS | {"colour": "Navy", "quantity": 1}
    sold_out = await update_cart(ctx, cart(navy))
    assert (
        "Cotton Panjabi (L/Navy): sold out (left: M, L/White, XL/White)" in sold_out.data["error"]
    )
    too_many = await update_cart(ctx, cart(PANJABIS | {"quantity": 4}))
    assert "Cotton Panjabi (L/White): only 3 left" in too_many.data["error"]
    any_colour = {"code": "panjabi-cotton", "size": "M", "quantity": 1}
    m_lines = [any_colour | {"colour": "White"}, any_colour | {"colour": "Navy", "quantity": 4}]
    assert (
        "(M): only 4 left" in (await update_cart(ctx, cart(*m_lines))).data["error"]
    )  # M is one count
    not_counted = await update_cart(ctx, cart(PANJABIS | {"colour": "Cream", "quantity": 9}))
    assert not_counted.ok  # L/Cream isn't listed

    await buy(world, agent, any_colour | {"colour": "Cream"})
    assert await stock(world, agent, "panjabi-cotton", "M", "Navy") == 3  # M, whatever colour
    assert await stock(world, agent, "panjabi-cotton", "L", "White") == 3  # untouched


async def test_an_exchange_to_a_sold_out_size_is_refused(world: World) -> None:
    agent = await world.agent("shop")
    await buy(world, agent, PANJABIS)  # L/White
    [order] = await orders(world)
    store = world.store(agent, order.conversation_id)
    for status in ("confirmed", "shipped", "delivered"):
        await store.move_order(order.id, status)

    navy = ExchangeInput(product="panjabi-cotton", colour="Navy")  # L/Navy is at 0
    refused = await request_exchange(make_ctx(store, agent=agent.config), navy)
    assert (
        "Cotton Panjabi (L/Navy) is sold out. Left: M, L/White, XL/White." in refused.data["error"]
    )


def test_a_csv_stock_cell_can_count_each_size_and_colour() -> None:
    sizes, colours = ["M", "L"], ["White", "Sky Blue"]
    assert stock_cell("", sizes, colours) == (None, [])
    assert stock_cell("12", sizes, colours) == (12, [])
    _, variants = stock_cell("M: 5; L sky blue: 0; L/White: 2", sizes, colours)
    assert [(v.size, v.colour, v.stock) for v in variants] == [
        ("M", "", 5),
        ("L", "Sky Blue", 0),
        ("L", "White", 2),
    ]
    with pytest.raises(ValueError, match="like M: 5"):
        stock_cell("XXL: 2", sizes, colours)


def test_rows_of_one_product_per_size_and_colour_become_one_product() -> None:
    csv = (
        "SKU,Product Name,Variant,Option,Price,Inventory\n"
        "SKU-001,Classic T-Shirt,Black,M,499,100\n"
        "SKU-002,Classic T-Shirt,White,L,499,80\n"
        "SKU-003,classic t-shirt,Black,XL,549,5\n"  # another price: its own product
        "SKU-004,Cap,Black,,399,60\n"
    )
    mapping = {"code": "SKU", "name": "Product Name", "colours": "Variant", "sizes": "Option"}
    shirt, big, cap = products_from_csv(csv, mapping | {"price": "Price", "stock": "Inventory"})
    assert (shirt.code, shirt.sizes, shirt.colours, shirt.stock) == (
        "SKU-001",
        ["M", "L"],
        ["Black", "White"],
        None,
    )
    assert [(v.size, v.colour, v.stock) for v in shirt.variants] == [
        ("M", "Black", 100),
        ("L", "White", 80),
        ("M", "White", 0),  # no row for it: the shop doesn't make it
        ("L", "Black", 0),
    ]
    assert "sold out: M/White, L/Black" in shirt.line("BDT")
    assert (big.code, big.price, big.stock) == ("SKU-003", 54900, 5)
    assert (cap.stock, cap.variants) == (60, [])  # one row stays as it was


# Photos (D57)


def photo(colour: str) -> bytes:
    """A small picture of one colour, as a phone would send it."""
    out = io.BytesIO()
    Image.new("RGB", (40, 40), colour).save(out, "PNG")
    return out.getvalue()


async def kurti_with_photo(client: AsyncClient) -> str:
    """Gives the shop's kurti a red photo; returns its URL. The dev agent must be the shop."""
    added = await client.post(
        "/api/products/photos", files={"file": ("kurti.png", photo("red"), "image/png")}
    )
    url: str = added.json()["url"]
    products = (await client.get("/api/products")).json()
    next(p for p in products if p["code"] == "kurti")["photos"] = [url]
    assert (await client.put("/api/products", json=products)).status_code == 200
    return url


async def test_a_photo_saved_before_doesnt_block_saving_the_list(
    app: FastAPI, client: AsyncClient
) -> None:
    """A photo kept on the disk before R2 was set has a /media address R2's check refuses
    (D101); saving the list keeps it, and only a new photo is checked."""
    await app.state.services.agents.ensure_dev_agent("shop", SEEDS / "shop.yaml")
    store = await app.state.services.agents.store(DEV_WORKSPACE_ID, "shop")
    old = f"/media/{DEV_WORKSPACE_ID}/products/old.jpg"  # not a name the check takes
    products = await store.products()
    products[0] = products[0].model_copy(update={"photos": [old]})
    await store.replace_products(products)

    listed = (await client.get("/api/products")).json()
    listed[1]["price"] = 99900
    assert (await client.put("/api/products", json=listed)).status_code == 200
    listed[1]["photos"] = [old.replace("old", "new")]
    assert (await client.put("/api/products", json=listed)).status_code == 422


def as_base64(data: bytes) -> str:
    return base64.b64encode(data).decode()


async def test_a_visitor_sends_a_photo_and_the_agent_shows_the_closest_product(
    app: FastAPI, client: AsyncClient
) -> None:
    services = app.state.services
    agent = await services.agents.ensure_dev_agent("shop", SEEDS / "shop.yaml")
    added = await client.post(
        "/api/products/photos", files={"file": ("kurti.png", photo("red"), "image/png")}
    )
    url = added.json()["url"]
    assert url.startswith(f"/media/{DEV_WORKSPACE_ID}/products/") and url.endswith(".jpg")
    assert (await client.get(url)).headers["content-type"] == "image/jpeg"

    products = (await client.get("/api/products")).json()
    kurti = next(p for p in products if p["code"] == "kurti")
    kurti["photos"] = [url]
    assert (await client.put("/api/products", json=products)).status_code == 200
    kurti["photos"] = ["/media/someone-else/products/x.jpg"]
    assert (await client.put("/api/products", json=products)).status_code == 422
    csv = "Name,Code,Price\nCotton Kurti,kurti,950\n"
    await client.post("/api/products/import", json={"csv": csv})
    kurti_now = next(p for p in (await client.get("/api/products")).json() if p["code"] == "kurti")
    assert kurti_now["photos"] == [url]  # kept

    llm = services.conversations.runtime.llm
    llm.script = [ToolCall("c1", "show_photos", {"products": ["kurti"]}), "Is it this one?"]
    body = {"visitor_id": "test-visitor", "message": "eta ache?", "photo": as_base64(photo("red"))}
    res = await client.post(f"/api/chat/{agent.public_key}", json=body)
    shown = next(
        json.loads(f.split("data: ")[1])
        for f in res.text.split("\n\n")
        if f.startswith("event: photos")
    )
    item = {"code": "kurti", "name": "Cotton Kurti", "price": "৳950", "url": url, "video": ""}
    assert shown == {"items": [item]}

    conv_id = res.text.split('"conversation_id": "')[1].split('"')[0]
    [_, said, _, tool, _] = (await services.conversations.get(DEV_WORKSPACE_ID, conv_id)).messages
    assert (
        said["content"] == "[Photo. Closest products in the shop: Cotton Kurti [kurti]]\neta ache?"
    )
    assert said["photo"].startswith(f"/media/{DEV_WORKSPACE_ID}/chat/")
    assert tool["photos"] == shown["items"]  # for the owner's view
    seen = (await client.get(f"/api/conversations/{conv_id}")).json()["messages"]
    assert (seen[1]["photo"], seen[2]["photos"]) == (said["photo"], shown["items"])
    sent = [m for r in llm.requests for m in r.messages]
    assert not any("photo" in m or "photos" in m for m in sent)  # the model never gets them

    llm.script = ["What is it?"]
    other = {"visitor_id": "test-visitor", "photo": as_base64(photo("blue"))}  # no words
    await client.post(f"/api/chat/{agent.public_key}", json=other)
    assert llm.requests[-1].messages[-1]["content"] == "[Photo. Nothing in the shop looks like it.]"


async def test_another_business_never_sees_your_photos(app: FastAPI, client: AsyncClient) -> None:
    agent = await app.state.services.agents.ensure_dev_agent("shop", SEEDS / "shop.yaml")
    second = await new_business(app)
    try:
        await second.post("/api/agent", json={"kind": "shop"})
        await second.post(
            "/api/products/photos", files={"file": ("tee.png", photo("green"), "image/png")}
        )
    finally:
        await second.aclose()
    llm = app.state.services.conversations.runtime.llm
    llm.script = ["What is it?"]
    body = {"visitor_id": "test-visitor", "photo": as_base64(photo("green"))}
    await client.post(f"/api/chat/{agent.public_key}", json=body)
    assert llm.requests[-1].messages[-1]["content"] == "[Photo. Nothing in the shop looks like it.]"


async def test_a_photo_that_cant_be_checked_still_gets_an_answer(world: World) -> None:
    agent = await world.agent("shop")
    world.embedder.fail_queries = True  # Gemini is down
    world.llm.script = ["What is it?"]
    turn = await world.services.conversations.start_turn(
        agent, None, "", "web", "web:test-visitor", photo=photo("red")
    )
    [_ async for _ in turn.events]
    said = (await world.conversation(turn.conversation_id)).messages[1]["content"]
    assert said == "[Photo. It couldn't be checked: ask what it is.]"


async def test_only_the_shop_takes_photos_and_only_real_ones(
    app: FastAPI, client: AsyncClient
) -> None:
    body = {"visitor_id": "test-visitor", "message": "hi", "photo": as_base64(photo("red"))}
    to_restaurant = await client.post(f"/api/chat/{DEV_KEY}", json=body)
    assert to_restaurant.status_code == 422 and "can't take photos" in to_restaurant.text

    agent = await app.state.services.agents.ensure_dev_agent("shop", SEEDS / "shop.yaml")
    not_a_photo = {**body, "photo": as_base64(b"hello, not a picture")}
    refused = await client.post(f"/api/chat/{agent.public_key}", json=not_a_photo)
    assert refused.status_code == 422 and refused.json()["error"]["code"] == "unreadable_photo"


async def test_show_photos_works_everywhere_but_a_call(world: World) -> None:
    agent = await world.agent("shop")
    store = world.store(agent)
    args = ShowPhotosInput(products=["kurti"])
    on_call = await show_photos(make_ctx(store, agent=agent.config, channel="voice"), args)
    assert not on_call.ok and "can't be shown here" in on_call.data["error"]
    no_photo = await show_photos(make_ctx(store, agent=agent.config), args)
    assert no_photo.photos and no_photo.photos[0]["url"] == ""  # a card with no picture (D125)
    assert no_photo.data["no_photo"] == ["Cotton Kurti"]


async def test_a_long_list_gives_the_groups_and_find_product_looks_them_up(world: World) -> None:
    """A WooCommerce store can have hundreds of products: over LONG_LIST, the prompt lists
    only the groups, and find_product returns the closest lines (D80)."""
    agent = await world.agent("shop")
    store = world.store(agent)
    tees = [
        Product(code=f"tee-{n}", name=f"Basic Tee {n}", price=50000, category="T-shirts")
        for n in range(LONG_LIST)
    ]
    await store.replace_products([*await store.products(), *tees])
    text = await SHOP.prompt(agent.config, store)

    assert "[kurti]" not in text and "[tee-1]" not in text
    assert "products, too many to list here. Groups: " in text
    assert f"T-shirts ({LONG_LIST})" in text
    assert "Look a product up with find_product" in text

    ctx = make_ctx(store, agent=agent.config)
    found = await find_product(ctx, FindProductInput(query="silk panjabi"))
    assert found.ok
    assert found.data["products"][0].startswith("- Silk Panjabi [panjabi-silk] ৳3,200")
    assert len((await find_product(ctx, FindProductInput(query="tee"))).data["products"]) == 8
    nothing = await find_product(ctx, FindProductInput(query="umbrella"))
    assert not nothing.ok and "Try other words" in nothing.data["error"]
    added = await update_cart(ctx, cart({"code": "tee-3", "quantity": 1}))
    assert added.ok  # the cart still takes any code


async def test_a_buyer_back_on_telegram_is_offered_their_last_details(world: World) -> None:
    """A new Telegram conversation from the same chat has the last order's name, mobile, area
    and address in the prompt, to offer again (D81). Its first conversation didn't."""
    agent = await world.agent("shop")
    talk = world.services.conversations
    world.llm.script = [
        ToolCall("c1", "update_cart", {"lines": [KURTI]}),
        ToolCall("c2", "place_order", DETAILS),
        READ_BACK,
    ]
    await talk.reply(agent, "telegram", "chat-7", "a black kurti, size M…")
    assert "# Their last order" not in world.llm.requests[0].system
    world.llm.script = [ToolCall("c3", "place_order", DETAILS), "Done: O-101."]
    await talk.reply(agent, "telegram", "chat-7", "yes")
    assert len(await orders(world)) == 1

    world.clock.advance(hours=13)
    world.llm.script = ["Hi again!"]
    await talk.reply(agent, "telegram", "chat-7", "aro ekta lagbe")
    system = world.llm.requests[-1].system
    assert "# Their last order" in system
    assert "name Kayes; mobile 01712345678; area Inside Dhaka; address House 12, Road 5" in system
    world.llm.script = [ToolCall("t", "track_order", {}), "It's received."]
    await talk.reply(agent, "telegram", "chat-7", "ager order ta kothay?")  # no code (D92)
    assert '"order": "O-101"' in str(world.llm.requests[-1].messages)

    world.llm.script = ["Hi!"]
    await talk.reply(agent, "telegram", "chat-8", "hello")  # another chat: nothing to offer
    assert "# Their last order" not in world.llm.requests[-1].system


async def test_a_second_order_in_the_same_chat_is_offered_the_first_ones_details(
    world: World,
) -> None:
    """Still in the same Messenger conversation after an order, the next one is offered its
    details too (D123): the agent shouldn't ask for them all again."""
    agent = await world.agent("shop")
    talk = world.services.conversations
    world.llm.script = [
        ToolCall("c1", "update_cart", {"lines": [KURTI]}),
        ToolCall("c2", "place_order", DETAILS),
        READ_BACK,
    ]
    await talk.reply(agent, "messenger", "psid-1", "a black kurti, size M…")
    world.llm.script = [ToolCall("c3", "place_order", DETAILS), "Done: O-101."]
    await talk.reply(agent, "messenger", "psid-1", "yes")
    assert len(await orders(world)) == 1

    world.llm.script = ["Ji, same details use korbo?"]
    await talk.reply(agent, "messenger", "psid-1", "aro ekta order korbo")
    system = world.llm.requests[-1].system
    assert "# Their last order" in system
    assert "name Kayes; mobile 01712345678" in system


async def test_photo_links_in_a_list_are_downloaded_and_indexed_once(world: World) -> None:
    """A CSV's, a sheet's or a WooCommerce store's photo links become the shop's own photos
    (D102): each downloaded the first time only, one that fails left out, and a product with
    no links keeps the photos it has."""
    await world.agent("shop")
    fetched: list[str] = []

    async def fetch(link: str) -> bytes:
        fetched.append(link)
        if link.endswith("gone.jpg"):
            raise ImageUnreachable("404 for a photo link")
        return photo("red")

    world.services.photos.fetch = fetch
    store = await world.services.agents.store(DEV_WORKSPACE_ID)
    ours = "/media/dev/products/" + "a" * 32 + ".jpg"  # the owner's own upload
    await store.replace_products([Product(code="kurti", name="Kurti", price=95000, photos=[ours])])
    csv = (
        "code,name,price,photos\n"
        "silk,Silk Panjabi,3200,https://cdn.example.com/silk.jpg https://cdn.example.com/gone.jpg\n"
        "kurti,Kurti,950,\n"
    )

    silk, kurti = await store.with_photos(products_from_csv(csv))
    [url] = silk.photos
    assert world.services.photos.is_products(DEV_WORKSPACE_ID, url)
    assert kurti.photos == [ours]
    matches = await world.services.photos.nearest(DEV_WORKSPACE_ID, to_jpeg(photo("red")))
    assert matches and matches[0][0] == url  # a visitor's photo finds it

    again, _ = await store.with_photos(products_from_csv(csv))  # the next sync
    assert again.photos == [url]
    assert fetched.count("https://cdn.example.com/silk.jpg") == 1
    assert fetched.count("https://cdn.example.com/gone.jpg") == 2  # tried again


async def test_find_product_shows_a_card_for_each_it_found(world: World) -> None:
    """A search shows its products as cards, photo or not, except on a call (D102, D125)."""
    agent = await world.agent("shop")
    store = world.store(agent)
    url = "/media/dev/products/" + "b" * 32 + ".jpg"
    products = await store.products()
    await store.replace_products(
        [
            p.model_copy(update={"photos": [url]}) if p.code == "panjabi-silk" else p
            for p in products
        ]
    )
    ctx = make_ctx(store, agent=agent.config)

    found = await find_product(ctx, FindProductInput(query="silk panjabi"))
    assert found.photos and found.photos[0]["url"] == url
    assert found.photos[0]["code"] == "panjabi-silk" and found.data["shown"][0] == "Silk Panjabi"
    assert [c["name"] for c in found.photos] == found.data["shown"]  # the rest have no photo
    assert all(c["url"] == "" for c in found.photos[1:])
    on_a_call = await find_product(
        make_ctx(store, agent=agent.config, channel="voice"), FindProductInput(query="silk")
    )
    assert on_a_call.photos is None and "shown" not in on_a_call.data


def bdt(minor: int, text: str) -> dict[str, object]:
    return {"minor": minor, "currency": "BDT", "text": text}


async def test_an_order_shows_a_summary_a_receipt_and_its_steps(world: World) -> None:
    agent = await world.agent("shop")
    store = world.store(agent)
    kurti = (await update_cart(make_ctx(store, agent=agent.config), cart(KURTI))).state
    ctx = make_ctx(store, state=kurti, agent=agent.config)
    args = PlaceOrderInput.model_validate(DETAILS)
    rows = [
        {"label": "Payment", "value": "paid in cash on delivery"},
        {"label": "Address", "value": DETAILS["address"]},
        {"label": "Name", "value": "Kayes"},
        {"label": "Mobile", "value": "01712345678"},
    ]

    asked = await place_order(ctx, args)
    assert asked.card == {  # D110: every number is Python's, as in the read-back
        "kind": "summary",
        "data": {
            "title": "Your order",
            "lines": [
                {"label": "Cotton Kurti (M, Black)", "qty": 1, "amount": bdt(95000, "৳950")},
                {"label": "Delivery (Inside Dhaka)", "amount": bdt(6000, "৳60")},
            ],
            "rows": rows,
            "total": bdt(101000, "৳1,010"),
        },
    }

    saved = await said_yes(place_order, ctx, args)
    assert saved.card == {
        "kind": "receipt",
        "data": {
            "title": "Order placed",
            "number": "O-101",
            "status": STATUS_WORDS["new"].capitalize(),
            "rows": [{"label": "Items", "value": "1 × Cotton Kurti (M, Black)"}, *rows],
            "total": bdt(101000, "৳1,010"),
        },
    }

    where = FindOrderInput(code="O-101", phone="01712345678")
    steps = (await track_order(ctx, where)).card
    assert steps and [(s["label"], s["done"], s["current"]) for s in steps["data"]["steps"]] == [
        ("Received", True, True),
        ("Confirmed", False, False),
        ("With the courier", False, False),
        ("Delivered", False, False),
    ]
    [order] = await orders(world)
    await store.move_order(order.id, "cancelled")
    cancelled = (await track_order(ctx, where)).card
    assert cancelled and cancelled["data"]["steps"] == [  # shown plainly
        {"label": "Cancelled", "done": True, "current": True}
    ]


async def test_the_cart_offers_what_goes_with_it_once(world: World) -> None:
    """`goes_with` (D125): one not in the cart, not sold out and in the shop, offered once."""
    agent = await world.agent("shop")
    store = world.store(agent)
    pairs = ["jamdani-saree", "lungi", "panjabi-cotton"]  # sold out, unknown, one to offer
    await store.replace_products(
        [
            p.model_copy(update={"goes_with": pairs}) if p.code == "kurti" else p
            for p in await store.products()
        ]
    )
    ctx = make_ctx(store, agent=agent.config)

    first = await update_cart(ctx, cart(KURTI))
    [offer] = first.data["goes_with"]
    assert offer.startswith("- Cotton Panjabi [panjabi-cotton] ৳1,850")
    assert first.state and first.state["offered"] == ["panjabi-cotton"]
    again = await update_cart(make_ctx(store, agent=agent.config, state=first.state), cart(KURTI))
    assert "goes_with" not in again.data  # offered already
    with_it = await update_cart(ctx, cart(KURTI, PANJABIS))
    assert "goes_with" not in with_it.data  # in the cart already


def test_videos_and_goes_with_come_from_a_csv_and_stay_when_a_file_has_none() -> None:
    text = "Name,Price,Videos,Goes_with\nCotton Panjabi,1850,https://youtu.be/abc x,pajama; tupi\n"
    [found] = products_from_csv(text)
    assert found.videos == ["https://youtu.be/abc"] and found.goes_with == ["pajama", "tupi"]

    [kept] = added([found], products_from_csv("Name,Price\nCotton Panjabi,1900\n"))
    assert (kept.price, kept.videos, kept.goes_with) == (190000, found.videos, found.goes_with)
    with pytest.raises(ValueError, match="isn't a link"):
        Product(code="x", name="X", price=1, videos=["youtu.be/abc"])


async def test_a_card_and_the_product_page_link_its_video(
    app: FastAPI, client: AsyncClient
) -> None:
    agent = await app.state.services.agents.ensure_dev_agent("shop", SEEDS / "shop.yaml")
    products = (await client.get("/api/products")).json()
    next(p for p in products if p["code"] == "kurti")["videos"] = ["https://youtu.be/kurti"]
    assert (await client.put("/api/products", json=products)).status_code == 200

    page = await client.get("/p/dev-shop/kurti?from=web")
    assert 'href="https://youtu.be/kurti"' in page.text and "Watch the video" in page.text
    store = await app.state.services.agents.store(DEV_WORKSPACE_ID, "shop")
    shown = await show_photos(
        make_ctx(store, agent=agent.config), ShowPhotosInput(products=["kurti"])
    )
    assert shown.photos and shown.photos[0]["video"] == "https://youtu.be/kurti"
    assert shown.data["video"] == ["Cotton Kurti"]
