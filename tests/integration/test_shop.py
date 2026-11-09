"""The shop: products with sizes, colours and stock; a cash-on-delivery order Python adds
up, saved only after a yes and taken out of stock, and confirmed by the shop's call or at once;
tracking it (the one from this chat, by the buyer's mobile, or with its code), changing
its items or details and cancelling it; an exchange request once it's delivered; the owner's products and orders routes; and photos: the owner's, a visitor's sent to
find a product, and the ones the agent shows."""

import asyncio
import base64
import csv
import html
import io
import json
import re
from datetime import timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from PIL import Image
from sqlalchemy import select

from heydesk.models import Conversation, Lead, TeamMessage, Turn
from heydesk.ports.llm import ToolCall
from heydesk.ports.media import ImageUnreachable
from heydesk.runtime.tool import ToolContext, ToolResult
from heydesk.services.agents import DEV_WORKSPACE_ID, LiveAgent, dev_workspace
from heydesk.services.photos import to_jpeg
from heydesk.shop import csv_import, shop_missing, shop_note, shop_prompt
from heydesk.shop.api import OfferIn, send_offer
from heydesk.shop.models import ShopOrder
from heydesk.shop.tools import (
    CartLine,
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
from heydesk.shop.values import (
    LONG_PRODUCT_LIST,
    PRODUCT_COLUMNS,
    STATUS_WORDS,
    Coupon,
    Product,
    merge_products,
    mobile,
    products_from_csv,
    stock_cell,
    with_district,
)
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
    """What's left of a product, or of its variant of that size and colour."""
    product = next(p for p in await world.store(agent).products() if p.code == code)
    stock_index = product.stock_index(size, colour)
    return None if stock_index is None else product.stock_at(stock_index)


async def place_an_order(app: FastAPI, client: AsyncClient) -> str:
    """A whole order on the chat API, with a yes. Returns the conversation id."""
    llm = app.state.services.conversations.llm
    llm.script = [
        ToolCall("c1", "update_cart", {"lines": [KURTI]}),
        ToolCall("c2", "place_order", DETAILS),
        READ_BACK,
        ToolCall("c3", "place_order", DETAILS),
        "Done! Your order number is O-101.",
    ]
    first = await client.post(
        f"/api/chat/{DEV_KEY}", json={"visitor_id": "test-visitor", "message": "a black kurti…"}
    )
    conv_id = first.text.split('"conversation_id": "')[1].split('"')[0]
    await client.post(
        f"/api/chat/{DEV_KEY}",
        json={"visitor_id": "test-visitor", "message": "yes", "conversation_id": conv_id},
    )
    # the turn's Woo push runs in the background on the test's one shared
    # connection: a session they close mid-test would roll back the test's own writes
    await asyncio.gather(*app.state.services.woo.tasks)
    return conv_id


async def buy(world: World, agent: LiveAgent, *lines: dict[str, object]) -> str:
    """A whole order over two turns, with a yes. Returns the order's code."""
    first = [
        ToolCall("c1", "update_cart", {"lines": list(lines)}),
        ToolCall("c2", "place_order", DETAILS),
    ]
    conversation = await world.say(agent, None, [*first, READ_BACK], "a black kurti, size M…")
    await world.say(
        agent, conversation, [ToolCall("c3", "place_order", DETAILS), "Done: O-101."], "yes"
    )
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
    agent = await world.agent()
    text = await shop_prompt(agent.config, world.store(agent))

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
    assert "# Size guide" not in await shop_prompt(no_guide, world.store(agent))
    assert "# The shop" in text  # it has policies
    assert await shop_missing(agent.config, world.store(agent)) == []

    added = await update_cart(make_ctx(world.store(agent), agent=agent.config), cart(KURTI))
    assert shop_note(agent.config, {}).startswith("\n# Language\nWrite your reply")
    assert "# The order so far" not in shop_note(agent.config, {})
    assert "1 × Cotton Kurti (M, Black), subtotal ৳950" in shop_note(agent.config, added.state)


async def test_python_checks_sizes_colours_and_stock_and_adds_up_the_cart(world: World) -> None:
    agent = await world.agent()
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
    """Python picks the buttons: L in Navy is sold out, so neither offers it."""
    agent = await world.agent()
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
    agent = await world.agent()
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
    agent = await world.agent()
    first = [
        ToolCall(
            "c1",
            "update_cart",
            {"lines": [KURTI | {"code": "panjabi-silk", "size": "M", "colour": "Gold"}]},
        )
    ]
    conversation = await world.say(
        agent, None, [*first, ToolCall("c2", "place_order", DETAILS), READ_BACK]
    )
    await world.say(
        agent, conversation, [READ_BACK], "hmm, maybe"
    )  # not a yes: the model asks again
    assert await orders(world) == []

    await world.say(agent, conversation, [ToolCall("c4", "place_order", DETAILS), "Done."], "yes")
    [saved] = await orders(world)
    assert (saved.code, saved.conversation_id, saved.status) == ("O-101", conversation, "new")
    assert (saved.subtotal, saved.delivery_charge, saved.total) == (320000, 0, 320000)  # free
    assert (saved.zone, saved.phone) == ("Inside Dhaka", "01712345678")
    assert await stock(world, agent, "panjabi-silk") == 1  # was 2
    assert (await world.conversation(conversation)).state["cart"] == {}


async def test_the_owner_hears_about_past_returns_and_what_is_running_low(world: World) -> None:
    agent = await world.agent()
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
    agent = await world.agent()
    code = await buy(world, agent, PANJABIS)
    assert await stock(world, agent, "panjabi-cotton", "L", "White") == 1  # was 3

    track = ToolCall("t", "track_order", {"code": "o 101", "phone": "+8801712345678"})
    conversation = await world.say(agent, None, [track, "It's received."])
    assert "the shop will call to confirm it" in await world.tool_said(conversation)
    stranger = ToolCall("t", "track_order", {"code": code, "phone": "01811111111"})
    conversation = await world.say(agent, None, [stranger, "Not found."])
    assert "No order matches that code and mobile number" in await world.tool_said(conversation)

    cancel = ToolCall("x", "cancel_order", {"code": code, "phone": "01712345678"})
    conversation = await world.say(agent, None, [cancel, "Cancel O-101: 2 Cotton Panjabi, ৳3,700?"])
    assert (
        "Cancel order O-101: 2 × Cotton Panjabi (L, White), total ৳3,700"
        in await world.tool_said(conversation)
    )
    await world.say(agent, conversation, [cancel, "Cancelled."], "yes")
    [cancelled] = await orders(world)
    assert cancelled.status == "cancelled"
    assert await stock(world, agent, "panjabi-cotton", "L", "White") == 3  # back in stock


async def test_the_owner_moves_orders_along_and_the_buyer_sees_it(
    app: FastAPI, client: AsyncClient
) -> None:
    services = app.state.services
    agent = await services.agents.ensure_dev_agent(SEEDS / "shop.yaml")
    assert (await client.get("/api/menu")).status_code == 404  # a shop has no menu
    assert len((await client.get("/api/products")).json()) == 6

    llm = services.conversations.llm
    llm.script = [
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
    assert order["source"] == "chat"
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
    assert team.json()["replies"] == [  # the buyer hears it in their chat
        "✅ Your order O-101 is confirmed and will be sent out.",
        "📦 Your order O-101 is with the courier. The delivery man calls before he comes.",
    ]

    store = await services.agents.store(DEV_WORKSPACE_ID)
    found = FindOrderInput(code="O-101", phone="01712345678")
    tracked = await track_order(make_ctx(store, agent=agent.config), found)
    assert (tracked.data["status"], tracked.data["usually_takes"]) == (
        STATUS_WORDS["shipped"],
        "1–2 days",
    )


async def test_products_come_from_a_csv(app: FastAPI, client: AsyncClient) -> None:
    await app.state.services.agents.ensure_dev_agent(SEEDS / "shop.yaml")
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
    """Another source's code for a product we have: the same name is the same product."""
    await app.state.services.agents.ensure_dev_agent(SEEDS / "shop.yaml")
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
    """Python matches the columns by their names, with no model; the owner's checked
    mapping is what the import takes."""
    await app.state.services.agents.ensure_dev_agent(SEEDS / "shop.yaml")
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
    agent = await world.agent()
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
    agent = await world.agent()
    no_call = agent.config.model_copy(update={"call_to_confirm": False})
    assert "one is confirmed as soon as it's placed: the shop doesn't call" in await shop_prompt(
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
    saved = type(no_call).model_validate(no_call.model_dump() | {"calls": calls.model_dump()})
    assert not saved.calls.enabled  # nothing waits for a call, so the calls go off with it


async def test_an_order_changes_before_the_courier_has_it_and_only_after_a_yes(
    world: World,
) -> None:
    agent = await world.agent()
    await buy(world, agent, KURTI)  # ৳950 inside Dhaka: ৳60 delivery, ৳1,010
    [order] = await orders(world)
    store = world.store(agent, order.conversation_id)

    def ctx(last_read_back: str = "") -> ToolContext[Any]:
        return make_ctx(store, agent=agent.config, last_read_back=last_read_back)

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

    two = ChangeOrderInput.model_validate({"items": [KURTI | {"quantity": 2}]})
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
    agent = await world.agent()
    await buy(world, agent, KURTI)
    [order] = await orders(world)
    async with world.sessions() as s, s.begin():  # as if the delivery charge was paid first
        (await s.get(ShopOrder, order.id)).paid = 6000  # type: ignore[union-attr]

    ctx = make_ctx(world.store(agent, order.conversation_id), agent=agent.config)
    refused = await change_order(ctx, ChangeOrderInput(district="Sylhet", address="Zindabazar"))
    assert "was paid online, so the area can't change" in refused.data["error"]


async def test_an_exchange_is_asked_for_within_the_days_after_delivery(world: World) -> None:
    agent = await world.agent()  # the seed takes exchanges for 3 days
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
    agent = await world.agent()  # Cotton Panjabi: M any colour 4, L/White 3, L/Navy 0,
    store = world.store(agent)  # XL/White 2; the rest isn't counted
    ctx = make_ctx(store, agent=agent.config)
    text = await shop_prompt(agent.config, store)
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
    agent = await world.agent()
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
    assert "sold out: M/White, L/Black" in shirt.prompt_line("BDT")
    assert (big.code, big.price, big.stock) == ("SKU-003", 54900, 5)
    assert (cap.stock, cap.variants) == (60, [])  # one row stays as it was


# Photos


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
    """A photo kept on the disk before R2 was set has a /media address R2's check refuses;
    saving the list keeps it, and only a new photo is checked."""
    await app.state.services.agents.ensure_dev_agent(SEEDS / "shop.yaml")
    store = await app.state.services.agents.store(DEV_WORKSPACE_ID)
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
    agent = await services.agents.ensure_dev_agent(SEEDS / "shop.yaml")
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

    llm = services.conversations.llm
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
    agent = await app.state.services.agents.ensure_dev_agent(SEEDS / "shop.yaml")
    second = await new_business(app)
    try:
        await second.post("/api/agent", json={})
        await second.post(
            "/api/products/photos", files={"file": ("tee.png", photo("green"), "image/png")}
        )
    finally:
        await second.aclose()
    llm = app.state.services.conversations.llm
    llm.script = ["What is it?"]
    body = {"visitor_id": "test-visitor", "photo": as_base64(photo("green"))}
    await client.post(f"/api/chat/{agent.public_key}", json=body)
    assert llm.requests[-1].messages[-1]["content"] == "[Photo. Nothing in the shop looks like it.]"


async def test_a_photo_that_cant_be_checked_still_gets_an_answer(world: World) -> None:
    agent = await world.agent()
    world.embedder.fail_queries = True  # Gemini is down
    world.llm.script = ["What is it?"]
    turn = await world.services.conversations.start_turn(
        agent, None, "", "web", "web:test-visitor", photo=photo("red")
    )
    [_ async for _ in turn.events]
    said = (await world.conversation(turn.conversation_id)).messages[1]["content"]
    assert said == "[Photo. It couldn't be checked: ask what it is.]"


async def test_only_a_real_photo_is_taken(client: AsyncClient) -> None:
    not_a_photo = {
        "visitor_id": "test-visitor",
        "message": "hi",
        "photo": as_base64(b"hello, not a picture"),
    }
    refused = await client.post(f"/api/chat/{DEV_KEY}", json=not_a_photo)
    assert refused.status_code == 422 and refused.json()["error"]["code"] == "unreadable_photo"


async def test_show_photos_works_everywhere_but_a_call(world: World) -> None:
    agent = await world.agent()
    store = world.store(agent)
    args = ShowPhotosInput(products=["kurti"])
    on_call = await show_photos(make_ctx(store, agent=agent.config, channel="voice"), args)
    assert not on_call.ok and "can't be shown here" in on_call.data["error"]
    no_photo = await show_photos(make_ctx(store, agent=agent.config), args)
    assert no_photo.photos and no_photo.photos[0]["url"] == ""  # a card with no picture
    assert no_photo.data["no_photo"] == ["Cotton Kurti"]


async def test_a_long_list_gives_the_groups_and_find_product_looks_them_up(world: World) -> None:
    """A WooCommerce store can have hundreds of products: over LONG_PRODUCT_LIST, the prompt lists
    only the groups, and find_product returns the closest lines."""
    agent = await world.agent()
    store = world.store(agent)
    tees = [
        Product(code=f"tee-{n}", name=f"Basic Tee {n}", price=50000, category="T-shirts")
        for n in range(LONG_PRODUCT_LIST)
    ]
    await store.replace_products([*await store.products(), *tees])
    text = await shop_prompt(agent.config, store)

    assert "[kurti]" not in text and "[tee-1]" not in text
    assert "products, too many to list here. Groups: " in text
    assert f"T-shirts ({LONG_PRODUCT_LIST})" in text
    assert "Look a product up with find_product" in text

    ctx = make_ctx(store, agent=agent.config)
    found = await find_product(ctx, FindProductInput(words="silk panjabi"))
    assert found.ok
    assert found.data["products"][0].startswith("- Silk Panjabi [panjabi-silk] ৳3,200")
    tees = await find_product(ctx, FindProductInput(group="t-shirts"))
    assert len(tees.data["products"]) == 8 and f"of {LONG_PRODUCT_LIST}" in tees.data["next"]
    nothing = await find_product(ctx, FindProductInput(words="umbrella"))
    assert not nothing.ok and "Try other words" in nothing.data["error"]
    wrong = await find_product(ctx, FindProductInput(group="Shoes"))
    assert not wrong.ok and "T-shirts" in wrong.data["error"]
    added = await update_cart(ctx, cart({"code": "tee-3", "quantity": 1}))
    assert added.ok  # the cart still takes any code


async def test_a_buyer_back_on_telegram_is_offered_their_last_details(world: World) -> None:
    """A new Telegram conversation from the same chat has the last order's name, mobile, area
    and address in the prompt, to offer again. Its first conversation didn't."""
    agent = await world.agent()
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
    await talk.reply(agent, "telegram", "chat-7", "ager order ta kothay?")  # no code
    assert '"order": "O-101"' in str(world.llm.requests[-1].messages)

    world.llm.script = ["Hi!"]
    await talk.reply(agent, "telegram", "chat-8", "hello")  # another chat: nothing to offer
    assert "# Their last order" not in world.llm.requests[-1].system


async def test_a_second_order_in_the_same_chat_is_offered_the_first_ones_details(
    world: World,
) -> None:
    """Still in the same Messenger conversation after an order, the next one is offered its
    details too: the agent shouldn't ask for them all again."""
    agent = await world.agent()
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
    """A CSV's, a sheet's or a WooCommerce store's photo links become the shop's own photos:
    each downloaded the first time only, one that fails left out, and a product with
    no links keeps the photos it has."""
    await world.agent()
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
    assert world.services.photos.is_product_photo(DEV_WORKSPACE_ID, url)
    assert kurti.photos == [ours]
    matches = await world.services.photos.nearest(DEV_WORKSPACE_ID, to_jpeg(photo("red")))
    assert matches and matches[0][0] == url  # a visitor's photo finds it

    again, _ = await store.with_photos(products_from_csv(csv))  # the next sync
    assert again.photos == [url]
    assert fetched.count("https://cdn.example.com/silk.jpg") == 1
    assert fetched.count("https://cdn.example.com/gone.jpg") == 2  # tried again


async def test_find_product_shows_a_card_for_each_it_found(world: World) -> None:
    """A search shows its products as cards, photo or not, except on a call."""
    agent = await world.agent()
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

    found = await find_product(ctx, FindProductInput(words="silk panjabi"))
    assert found.photos and found.photos[0]["url"] == url
    assert found.photos[0]["code"] == "panjabi-silk" and found.data["shown"][0] == "Silk Panjabi"
    assert [c["name"] for c in found.photos] == found.data["shown"]  # the rest have no photo
    assert all(c["url"] == "" for c in found.photos[1:])
    on_a_call = await find_product(
        make_ctx(store, agent=agent.config, channel="voice"), FindProductInput(words="silk")
    )
    assert on_a_call.photos is None and "shown" not in on_a_call.data


async def test_find_product_looks_in_the_group_and_shows_only_what_has_every_word(
    world: World,
) -> None:
    """Asked "logitech er headphone ase?": only Logitech headphones are shown. With none, the
    keyboards that say Logitech aren't shown as cards, and the other headphones are only told."""
    agent = await world.agent()
    store = world.store(agent)

    def item(code: str, name: str, group: str) -> Product:
        return Product(code=code, name=name, price=100000, category=group)

    gear = [
        item("mx-keys", "Logitech MX Keys", "Keyboards"),
        item("m185", "Logitech M185", "Mice"),
        item("g435", "Logitech G435", "Headphones"),
        item("xm5", "Sony WH-1000XM5", "Headphones"),
    ]
    await store.replace_products(gear)
    ctx = make_ctx(store, agent=agent.config)

    found = await find_product(ctx, FindProductInput(group="Headphones", words="logitech"))
    assert found.data["shown"] == ["Logitech G435"]
    assert [c["code"] for c in found.photos or []] == ["g435"]

    await store.replace_products([g for g in gear if g.code != "g435"])
    none = await find_product(ctx, FindProductInput(group="Headphones", words="logitech"))
    assert none.photos is None and "shown" not in none.data
    assert "Sony WH-1000XM5" in none.data["closest"][0]  # the group's others, to offer
    loose = await find_product(ctx, FindProductInput(words="logitech headphone"))
    assert loose.photos is None and "closest" in loose.data  # the keyboards stay off the screen


def bdt(minor: int, text: str) -> dict[str, object]:
    return {"minor": minor, "currency": "BDT", "text": text}


async def test_an_order_shows_a_summary_a_receipt_and_its_steps(world: World) -> None:
    agent = await world.agent()
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
    assert asked.card == {  # every number is Python's, as in the read-back
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
    assert saved.card
    [order] = await orders(world)
    assert saved.card["data"].pop("order_url").endswith(f"/o/{order.id}")  # its page
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
        ("Packed", False, False),
        ("With the courier", False, False),
        ("Delivered", False, False),
    ]
    await store.move_order(order.id, "cancelled")
    cancelled = (await track_order(ctx, where)).card
    assert cancelled and cancelled["data"]["steps"] == [  # shown plainly
        {"label": "Cancelled", "done": True, "current": True}
    ]


async def test_the_cart_offers_what_goes_with_it_once(world: World) -> None:
    """`goes_with`: one not in the cart, not sold out and in the shop, offered once."""
    agent = await world.agent()
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

    [kept] = merge_products([found], products_from_csv("Name,Price\nCotton Panjabi,1900\n"))
    assert (kept.price, kept.videos, kept.goes_with) == (190000, found.videos, found.goes_with)
    with pytest.raises(ValueError, match="isn't a link"):
        Product(code="x", name="X", price=1, videos=["youtu.be/abc"])


async def test_a_card_and_the_product_page_link_its_video(
    app: FastAPI, client: AsyncClient
) -> None:
    agent = await app.state.services.agents.ensure_dev_agent(SEEDS / "shop.yaml")
    products = (await client.get("/api/products")).json()
    next(p for p in products if p["code"] == "kurti")["videos"] = ["https://youtu.be/kurti"]
    assert (await client.put("/api/products", json=products)).status_code == 200

    page = await client.get("/p/dev-shop/kurti")
    assert 'href="https://youtu.be/kurti"' in page.text and "Watch the video" in page.text
    store = await app.state.services.agents.store(DEV_WORKSPACE_ID)
    shown = await show_photos(
        make_ctx(store, agent=agent.config), ShowPhotosInput(products=["kurti"])
    )
    assert shown.photos and shown.photos[0]["video"] == "https://youtu.be/kurti"
    assert shown.data["video"] == ["Cotton Kurti"]


async def test_the_product_page_shows_the_sale_the_stock_and_a_preview(
    app: FastAPI, client: AsyncClient
) -> None:
    """The saving, how many are left, the sizes as chips to tap, and a pasted link's
    preview."""
    await app.state.services.agents.ensure_dev_agent(SEEDS / "shop.yaml")
    url = await kurti_with_photo(client)
    products = (await client.get("/api/products")).json()
    next(p for p in products if p["code"] == "kurti").update(was_price=125000, stock=3)
    assert (await client.put("/api/products", json=products)).status_code == 200

    body = (await client.get("/p/dev-shop/kurti")).text
    assert "Save 24%" in body and "Only 3 left" in body
    assert 'type="radio" name="size" value="M"' in body
    assert '<meta property="og:image" content="http' in body and url in body


async def test_buy_now_on_the_product_page_saves_once_at_place_order(
    app: FastAPI, client: AsyncClient
) -> None:
    """The form is read back first, and saved only at *Place order*, with Python's total; the
    receipt goes into the chat the page was opened from and links the order's page, which
    doesn't show the buyer's details."""
    services = app.state.services
    await services.agents.ensure_dev_agent(SEEDS / "shop.yaml")
    services.conversations.llm.script = ["Hello!"]
    res = await client.post("/api/chat/dev-shop", json={"visitor_id": "a-buyer-1", "message": "hi"})
    conversation = res.text.split('"conversation_id": "')[1].split('"')[0]
    url = f"/p/dev-shop/kurti?c={conversation}"
    assert "Buy now" in (await client.get(url)).text

    async def saved() -> list[ShopOrder]:
        async with services.conversations.sessions() as s:
            return list(await s.scalars(select(ShopOrder)))

    form = {
        "size": "M",
        "colour": "Black",
        "quantity": "2",
        "name": "Kayes",
        "phone": "০১৭১২-৩৪৫৬৭৮",
        "address": "House 12, Road 5, Mirpur 10",
        "district": "Dhaka",
    }
    check = await client.post(url, data=form)
    assert "Check your order" in check.text and "৳1,960" in check.text  # 2 × ৳950 + ৳60
    assert await saved() == []
    found = re.search(r'name="heard" value="([^"]*)"', check.text)
    assert found
    heard = html.unescape(found[1])

    wrong = await client.post(url, data=form | {"heard": heard.replace("৳1,960", "৳10")})
    assert "Something changed" in wrong.text and await saved() == []

    placed = await client.post(url, data=form | {"heard": heard})
    assert placed.status_code == 303
    [order] = await saved()
    assert (order.total, order.phone, order.conversation_id) == (
        196000,
        "01712345678",
        conversation,
    )
    assert placed.headers["location"].endswith(f"/o/{order.id}")
    assert order.address == "House 12, Road 5, Mirpur 10, Dhaka"  # the district box is kept

    page = await client.get(f"/o/{order.id}")
    assert order.code in page.text and "৳1,960" in page.text
    assert "Kayes" not in page.text and "01712345678" not in page.text
    replies = (await client.get(f"/api/chat/dev-shop/{conversation}/team")).json()["replies"]
    assert order.code in replies[-1] and f"/o/{order.id}" in replies[-1]

    assert (await client.get("/o/no-such-order")).status_code == 404
    assert (await client.post("/p/dev-shop/no-such", data=form)).status_code == 404


async def test_a_form_with_a_problem_comes_back_filled_in(
    app: FastAPI, client: AsyncClient
) -> None:
    await app.state.services.agents.ensure_dev_agent(SEEDS / "shop.yaml")
    form = {"size": "M", "colour": "Black", "name": "Kayes", "phone": "123"}
    again = await client.post("/p/dev-shop/kurti", data=form | {"address": "Mirpur 10"})
    assert "Please check your mobile number" in again.text and 'value="Kayes"' in again.text


async def test_the_team_types_an_order_and_takes_it_to_money_in(
    app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A buyer who called: the team's order is checked and added up as a chat's, saved
    confirmed with no yes, and the owner hears of it. It goes on hold, then packed, and ends
    delivered with the cash in, or returned with a reason; not before it ends."""
    services = app.state.services
    await services.agents.ensure_dev_agent(SEEDS / "shop.yaml")
    told: list[str] = []

    async def notify(workspace_id: str, text: str) -> None:
        told.append(text)

    monkeypatch.setattr(services.team, "notify", notify)
    body = {
        "lines": [{"code": "kurti", "quantity": 2, "size": "M", "colour": "Black"}],
        "name": "Rahim",
        "phone": "01812345678",
        "address": "Road 3, Patenga",
        "district": "Chattogram",
    }
    res = await client.post("/api/shop/orders", json=body)
    assert res.status_code == 201
    order = res.json()
    assert order["source"] == "team"
    assert (order["status"], order["total"], order["conversation_id"]) == (
        "confirmed",
        202000,  # 2 × ৳950 + ৳120 outside Dhaka, from Python
        None,
    )
    assert told and "typed by the team" in told[0] and order["code"] in told[0]
    wrong = await client.post("/api/shop/orders", json=body | {"phone": "123"})
    assert wrong.status_code == 422

    url = f"/api/shop/orders/{order['id']}"
    assert (await client.patch(url, json={"paid_in": True})).status_code == 409  # not ended
    for status in ("on_hold", "confirmed", "packed", "shipped"):
        assert (await client.patch(url, json={"status": status})).json()["status"] == status
    ended = await client.patch(url, json={"status": "delivered", "paid_in": True})
    assert (ended.json()["status"], ended.json()["paid_in"]) == ("delivered", True)

    second = (await client.post("/api/shop/orders", json=body)).json()
    url = f"/api/shop/orders/{second['id']}"
    await client.patch(url, json={"status": "shipped"})
    back = await client.patch(url, json={"status": "returned", "reason": "  refused  "})
    assert back.json()["reason"] == "refused"


async def test_a_held_order_isnt_booked_with_the_courier(world: World) -> None:
    agent = await world.agent()
    await buy(world, agent, KURTI)
    [order] = await orders(world)
    await world.store(agent).move_order(order.id, "on_hold")
    await world.services.courier.sync(DEV_WORKSPACE_ID)
    assert (await orders(world))[0].parcel_id == ""
    await world.store(agent).move_order(order.id, "confirmed")
    await world.store(agent).move_order(order.id, "packed")
    await world.services.courier.sync(DEV_WORKSPACE_ID)
    assert (await orders(world))[0].status == "shipped"  # packed is booked


async def test_orders_export_as_the_list_shows_them(app: FastAPI, client: AsyncClient) -> None:
    """The filtered rows, money in taka, Dhaka time, a formula kept as words; and no other
    business's."""
    await app.state.services.agents.ensure_dev_agent(SEEDS / "shop.yaml")
    body = {
        "lines": [{"code": "kurti", "quantity": 1, "size": "M", "colour": "Black"}],
        "name": "=HYPERLINK(1)",
        "phone": "01812345678",
        "address": "Mirpur 10",
        "district": "Dhaka",
    }
    first = (await client.post("/api/shop/orders", json=body)).json()
    second = (await client.post("/api/shop/orders", json=body | {"name": "Nila"})).json()
    await client.patch(f"/api/shop/orders/{second['id']}", json={"status": "cancelled"})

    res = await client.get("/api/shop/orders.csv", params={"status": "confirmed"})
    assert res.headers["content-type"].startswith("text/csv")
    rows = list(csv.reader(io.StringIO(res.text.removeprefix("﻿"))))
    assert rows[0][:3] == ["Order", "Date", "Status"] and len(rows) == 2
    row = dict(zip(rows[0], rows[1], strict=True))
    assert (row["Order"], row["Name"], row["Total"]) == (first["code"], "'=HYPERLINK(1)", "1010")
    assert row["Mobile"] == "01812-345678"  # not a number, so the 0 stays
    assert row["Date"].startswith(FIXED_NOW.astimezone(ZoneInfo("Asia/Dhaka")).strftime("%Y-%m-%d"))

    other = await new_business(app)
    assert (await other.post("/api/agent", json={})).status_code == 201
    theirs = await other.get("/api/shop/orders.csv")
    assert len(list(csv.reader(io.StringIO(theirs.text.removeprefix("﻿"))))) == 1


async def test_each_order_shows_its_buyers_record_and_buyers_are_listed(
    app: FastAPI, client: AsyncClient
) -> None:
    """By mobile: how many orders, and how they ended; another business's orders with the same
    mobile never count."""
    await app.state.services.agents.ensure_dev_agent(SEEDS / "shop.yaml")
    body = {
        "lines": [{"code": "kurti", "quantity": 1, "size": "M", "colour": "Black"}],
        "name": "Rahim",
        "phone": "01812345678",
        "address": "Mirpur 10",
        "district": "Dhaka",
    }
    ids = [(await client.post("/api/shop/orders", json=body)).json()["id"] for _ in range(3)]
    await client.patch(f"/api/shop/orders/{ids[0]}", json={"status": "shipped"})
    await client.patch(f"/api/shop/orders/{ids[0]}", json={"status": "returned"})
    await client.patch(f"/api/shop/orders/{ids[1]}", json={"status": "cancelled"})
    await client.post("/api/shop/orders", json=body | {"phone": "01712345678", "name": "Nila"})

    other = await new_business(app)
    assert (await other.post("/api/agent", json={})).status_code == 201
    await other.put("/api/products", json=[{"code": "kurti", "name": "Kurti", "price": 50000}])
    theirs = body | {"lines": [{"code": "kurti", "quantity": 1}]}
    everywhere = {"delivery": [{"name": "Everywhere", "charge": 5000}]}
    assert (await other.patch("/api/agent", json=everywhere)).status_code == 200
    assert (await other.post("/api/shop/orders", json=theirs)).status_code == 201

    order = (await client.get(f"/api/shop/orders/{ids[2]}")).json()
    assert order["buyer"] == {"orders": 3, "delivered": 0, "returned": 1, "cancelled": 1}
    listed = (await client.get("/api/shop/orders")).json()
    assert {o["phone"]: o["buyer"]["orders"] for o in listed} == {
        "01812345678": 3,
        "01712345678": 1,
    }

    buyers = (await client.get("/api/shop/buyers")).json()
    assert [b["phone"] for b in buyers] == ["01712345678", "01812345678"]  # latest first
    rahim = buyers[1]
    assert (rahim["name"], rahim["orders"], rahim["returned"], rahim["channels"]) == (
        "Rahim",
        3,
        1,
        [],  # typed by the team: no chat
    )
    assert [b["name"] for b in (await client.get("/api/shop/buyers?q=nila")).json()] == ["Nila"]


async def coupon_order(world: World, agent: LiveAgent, code: str) -> ToolResult:
    """1 Cotton Kurti (৳950) to Mirpur (৳60) with `code`, said yes to."""
    store = world.store(agent)
    ctx = make_ctx(store, agent=agent.config)
    cart_result = await update_cart(ctx, UpdateCartInput(lines=[CartLine(**KURTI)], coupon=code))
    ctx = make_ctx(store, agent=agent.config, state=cart_result.state)
    # the same kurti each time: on purpose, so not asked about
    return await said_yes(place_order, ctx, PlaceOrderInput(**DETAILS, again=True))


async def test_a_coupon_takes_its_discount_off_the_bill(world: World) -> None:
    """Percent, amount and free delivery, each worked out by Python and read back; the order
    keeps the discount and the code counts a use."""
    agent = await world.agent()
    store = world.store(agent)
    await store.add_coupon(Coupon(code="eid10", kind="percent", value=10))
    await store.add_coupon(Coupon(code="FLAT100", kind="amount", value=10000))
    await store.add_coupon(Coupon(code="SHIPFREE", kind="free_delivery"))

    ctx = make_ctx(store, agent=agent.config)
    cart_result = await update_cart(ctx, UpdateCartInput(lines=[CartLine(**KURTI)], coupon="Eid10"))
    assert cart_result.ok
    assert (cart_result.data["coupon"], cart_result.data["discount"]) == ("EID10: 10% off", "−৳95")
    ctx = make_ctx(store, agent=agent.config, state=cart_result.state)
    asked = await place_order(ctx, PlaceOrderInput(**DETAILS))
    assert "coupon EID10 −৳95" in asked.data["read_back"]
    assert "total ৳915" in asked.data["read_back"]  # 950 − 95 + 60

    for code in ("EID10", "FLAT100", "SHIPFREE"):
        placed = await coupon_order(world, agent, code)
        assert placed.ok, placed.data
    saved = sorted(await orders(world), key=lambda o: o.number)
    assert [(o.coupon, o.total) for o in saved] == [
        ("EID10", 91500),
        ("FLAT100", 91000),
        ("SHIPFREE", 95000),
    ]
    assert (saved[0].discount, saved[2].delivery_charge) == (9500, 0)
    assert {c.code: c.used for c in await store.coupons()} == {
        "EID10": 1,
        "FLAT100": 1,
        "SHIPFREE": 1,
    }


async def test_an_ended_or_used_up_coupon_keeps_the_order_without_it(world: World) -> None:
    agent = await world.agent()
    store = world.store(agent)
    await store.add_coupon(
        Coupon(code="OLD", kind="percent", value=10, ends_at=FIXED_NOW - timedelta(days=1))
    )
    await store.add_coupon(Coupon(code="ONCE", kind="amount", value=5000, max_uses=1))
    ctx = make_ctx(store, agent=agent.config)

    for code, why in (("OLD", "has ended"), ("NOPE", "no coupon NOPE")):
        result = await update_cart(ctx, UpdateCartInput(lines=[CartLine(**KURTI)], coupon=code))
        assert not result.ok and why in result.data["coupon_problem"]
        assert result.state["cart"]["lines"] and "coupon" not in result.state["cart"]

    assert (await coupon_order(world, agent, "ONCE")).ok
    again = await update_cart(ctx, UpdateCartInput(lines=[CartLine(**KURTI)], coupon="ONCE"))
    assert not again.ok and "used up" in again.data["coupon_problem"]


async def test_an_order_read_back_with_no_yes_gets_one_nudge(world: World) -> None:
    """1–3 hours after a read-back with no yes, the buyer gets one nudge with the read-back's
    buttons; never a second, and none for a chat whose order was placed."""
    agent = await world.agent()
    first = [ToolCall("c1", "update_cart", {"lines": [KURTI]})]
    waiting = await world.say(
        agent, None, [*first, ToolCall("c2", "place_order", DETAILS), READ_BACK]
    )
    nudge = world.services.conversations.nudge_unfinished
    world.clock.advance(minutes=30)
    await nudge()  # too soon
    assert (await world.conversation(waiting)).messages[-1]["content"] == READ_BACK

    world.clock.advance(hours=1)
    await nudge()
    await nudge()
    conversation = await world.conversation(waiting)
    said = [m["content"] for m in conversation.messages if m.get("by") == "team"]
    assert said == ["Shall I confirm your order? Tap Yes, or tell me what to change."]
    assert conversation.nudged


async def test_a_placed_order_is_not_nudged(world: World) -> None:
    agent = await world.agent()
    await buy(world, agent, KURTI)
    world.clock.advance(hours=2)
    await world.services.conversations.nudge_unfinished()
    async with world.sessions() as s:
        assert [c.nudged for c in await s.scalars(select(Conversation))] == [False]


async def test_an_offer_goes_to_who_wrote_today_and_only_they_get_it(world: World) -> None:
    """The offer reaches the chats from the last 24 hours, and `update_cart` takes it off for
    them alone, until it ends."""
    agent = await world.agent()
    yesterday = await world.say(agent, None, ["Hello!"], "hi")
    world.clock.advance(hours=25)
    today = await world.say(agent, None, ["Hello!"], "hi")
    ends = world.clock.now + timedelta(hours=12)
    offer = OfferIn(text="Free delivery till 10 pm!", kind="free_delivery", ends_at=ends)
    sent = await send_offer(offer, world.store(agent), world.services)
    assert (sent.code, sent.sent_to) == ("OFFER1", 1)
    assert (await world.conversation(today)).messages[-1]["content"] == offer.text

    def cart_in(conversation: str, coupon: str = "") -> Any:
        ctx = make_ctx(world.store(agent, conversation), agent=agent.config, now=world.clock.now)
        return update_cart(ctx, UpdateCartInput(lines=[CartLine(**KURTI)], coupon=coupon))

    got = await cart_in(today)
    assert got.state["cart"]["coupon"]["code"] == "OFFER1" and got.data["delivery"] == "free"
    missed = await cart_in(yesterday)
    assert "coupon" not in missed.state["cart"]
    assert not (await cart_in(yesterday, "offer1")).ok  # its code is no use to them

    world.clock.advance(hours=13)
    assert "coupon" not in (await cart_in(today)).state["cart"]  # it has ended


async def test_an_offer_reaches_as_far_back_as_the_owner_picks(world: World) -> None:
    """`within_hours=48` reaches a web chat from yesterday too; the default doesn't."""
    agent = await world.agent()
    await world.say(agent, None, ["Hello!"], "hi")
    world.clock.advance(hours=30)
    ends = world.clock.now + timedelta(hours=12)
    offer = OfferIn(text="Eid sale!", kind="percent", value=10, ends_at=ends)
    assert (await send_offer(offer, world.store(agent), world.services)).sent_to == 0
    wider = offer.model_copy(update={"within_hours": 48})
    assert (await send_offer(wider, world.store(agent), world.services)).sent_to == 1


async def test_the_dashboard_adds_up_one_business_today_yesterday_and_the_week(
    app: FastAPI, client: AsyncClient
) -> None:
    """Chats answered and handed off, sales and their total, chats that bought, where the
    orders came from and open leads, in Dhaka's days; another business's rows aren't
    counted."""
    agent = await app.state.services.agents.ensure_dev_agent(SEEDS / "shop.yaml")
    yesterday = FIXED_NOW - timedelta(days=1)  # FIXED_NOW is 14:00 in Dhaka
    workspace_id, other = DEV_WORKSPACE_ID, "other"

    def order(n: int, total: int, conversation: str | None, source: str, **kw: Any) -> ShopOrder:
        fields: dict[str, Any] = {
            "workspace_id": workspace_id,
            "number": n,
            "name": "R",
            "phone": "01812345678",
            "lines": [],
            "subtotal": total,
            "delivery_charge": 0,
            "total": total,
            "currency": "BDT",
            "zone": "Dhaka",
            "address": "x",
            "conversation_id": conversation,
            "source": source,
            "created_at": FIXED_NOW,
        }
        return ShopOrder(**fields | kw)

    def turn(conversation: str, at: Any, handed_off: bool = False) -> Turn:
        return Turn(
            workspace_id=workspace_id,
            conversation_id=conversation,
            channel="web",
            llm="fake",
            at=at,
            total_ms=1,
            first_output_ms=None,
            input_tokens=0,
            output_tokens=0,
            cost_micros=0,
            tools=[],
            handed_off=handed_off,
            interrupted=False,
        )

    def lead(n: int, status: str) -> Lead:
        return Lead(
            workspace_id=workspace_id,
            number=n,
            name="R",
            phone="01812345678",
            call_at=FIXED_NOW,
            note="",
            status=status,
            created_at=FIXED_NOW,
        )

    async with app.state.sessions() as s, s.begin():
        s.add(dev_workspace(other, FIXED_NOW))
        for conversation in ("a", "b", "c"):
            s.add(
                Conversation(
                    id=conversation,
                    workspace_id=workspace_id,
                    agent_id=agent.agent_id,
                    channel="web",
                )
            )
        s.add_all(
            [
                turn("a", FIXED_NOW),
                turn("b", FIXED_NOW, handed_off=True),
                turn("b", FIXED_NOW),
                turn("c", yesterday),
                order(101, 1000, "a", "chat"),
                order(102, 2000, None, "team"),
                order(103, 500, "a", "page"),
                order(104, 9999, "b", "chat", status="cancelled"),
                order(105, 300, "c", "chat", created_at=yesterday),
                order(101, 7777, None, "team", workspace_id=other),
                lead(101, "open"),
                lead(102, "sold"),
            ]
        )

    stats = (await client.get("/api/stats", params={"days": 7})).json()
    assert stats["currency"] == "BDT"
    assert stats["today"] == {
        "conversations": 2,
        "handed_off": 1,
        "sales": 3,
        "amount": 3500,
        "bought": 1,
        "sources": {"chat": 1, "team": 1, "page": 1},
        "leads": 1,
    }
    assert (stats["yesterday"]["conversations"], stats["yesterday"]["amount"]) == (1, 300)
    week = stats["period"]
    assert (week["conversations"], week["sales"], week["amount"], week["bought"]) == (3, 4, 3800, 2)

    # The days one by one, in the business's own days: the cancelled order isn't in them.
    daily = stats["daily"]
    assert len(daily) == 7
    assert daily[-1] == {"start": "2026-09-29T00:00:00+06:00", "sales": 3, "amount": 3500}
    assert (daily[-2]["sales"], daily[-2]["amount"]) == (1, 300)
    assert sum(d["amount"] for d in daily[:-2]) == 0


async def test_the_same_mobile_and_items_again_is_asked_about_first(world: World) -> None:
    """A second order of the same items from the same mobile within DUPLICATE_HOURS isn't
    read back until they say it's on purpose."""
    agent = await world.agent()
    store = world.store(agent)

    async def kurti_ctx() -> ToolContext[Any]:
        state = (await update_cart(make_ctx(store, agent=agent.config), cart(KURTI))).state
        return make_ctx(store, state=state, agent=agent.config)

    async def order_again(again: bool = False) -> ToolResult:
        args = PlaceOrderInput(**DETAILS, again=again)
        return await said_yes(place_order, await kurti_ctx(), args)

    assert (await order_again()).ok
    asked = await place_order(await kurti_ctx(), PlaceOrderInput(**DETAILS))
    assert not asked.ok and "already ordered the same items as O-101" in str(asked.data)
    assert len(await orders(world)) == 1
    assert (await order_again(again=True)).ok
    assert len(await orders(world)) == 2


def test_the_district_is_in_the_saved_address_once() -> None:
    assert with_district("House 12, Mirpur 10", "dhaka district") == "House 12, Mirpur 10, Dhaka"
    assert with_district("Zindabazar, Sylhet", "Sylhet") == "Zindabazar, Sylhet"
    assert with_district("জিন্দাবাজার, সিলেট", "Sylhet") == "জিন্দাবাজার, সিলেট"


async def test_the_inbox_counts_new_orders_and_messages_and_shows_the_last_line(
    app: FastAPI, client: AsyncClient
) -> None:
    conv_id = await place_an_order(app, client)
    store = await app.state.services.agents.store(DEV_WORKSPACE_ID)
    await store.add(TeamMessage, name="", phone="", body="Call me", best_time="")

    counts = (await client.get("/api/conversations/counts")).json()
    assert (counts["orders_new"], counts["messages_new"]) == (1, 1)
    [message] = (await client.get("/api/messages")).json()
    await client.patch(f"/api/messages/{message['id']}", json={"status": "handled"})
    assert (await client.get("/api/conversations/counts")).json()["messages_new"] == 0

    [row] = (await client.get("/api/conversations")).json()
    assert (row["id"], row["preview"]) == (conv_id, "Done! Your order number is O-101.")
