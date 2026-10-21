"""The restaurant desk: its menu in the prompt, an order Python adds up, saved only after a
read-back and a yes, table bookings with the seats counted by Python, and the owner's menu,
orders and bookings routes."""

import asyncio
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select

from heydesk.desks.restaurant import RESTAURANT
from heydesk.desks.restaurant.models import Booking, Order
from heydesk.desks.restaurant.tools import (
    BookTableInput,
    PlaceOrderInput,
    UpdateOrderInput,
    book_table,
    place_order,
)
from heydesk.desks.restaurant.tools import update_order as update
from heydesk.desks.restaurant.values import has_room
from heydesk.ports.llm import ToolCall
from heydesk.runtime.schedule import WeeklyHours
from heydesk.services.agents import DEV_WORKSPACE_ID
from tests.conftest import AGENT, CART, DEV_KEY, World, make_ctx

LINES = [{"code": "chk-biryani", "quantity": 2}, {"code": "borhani", "quantity": 1}]
DETAILS = {"time": "7:30 pm", "name": "Kayes", "phone": "01712345678"}
READ_BACK = "2 Chicken Biryani and 1 Borhani, ৳1,030, at 7:30 pm for Kayes. Shall I place it?"
YES = {"read_back": 0.95, "said_yes": 0.95}
NO = {"read_back": 0.1, "said_yes": 0.1}
TABLE = {"people": 4, "time": "tomorrow at 8 pm", "name": "Kayes", "phone": "01712345678"}
WED_8PM = datetime(2026, 9, 30, 14, 0, tzinfo=UTC)  # 8 pm in Dhaka, the day after FIXED_NOW


async def place_an_order(app: FastAPI, client: AsyncClient) -> str:
    """A whole order on the chat API, with a yes. Returns the conversation id."""
    runtime = app.state.services.conversations.runtime
    runtime.llm.script = [
        ToolCall("c1", "update_order", {"lines": LINES}),
        ToolCall("c2", "place_order", DETAILS),
        READ_BACK,
        ToolCall("c3", "place_order", DETAILS),
        "Done! Your order number is 101.",
    ]
    runtime.decider.script = [{"read_back": 0.1, "said_yes": 0.1}, YES]
    first = await client.post(
        f"/api/chat/{DEV_KEY}", json={"visitor_id": "test-visitor", "message": "two biryanis…"}
    )
    conv_id = first.text.split('"conversation_id": "')[1].split('"')[0]
    await client.post(
        f"/api/chat/{DEV_KEY}",
        json={"visitor_id": "test-visitor", "message": "yes", "conversation_id": conv_id},
    )
    # the turn's Woo push and courier round run in the background on the test's one shared
    # connection: a session they close mid-test would roll back the test's own writes
    services = app.state.services
    await asyncio.gather(*services.woo.tasks, *services.courier.tasks)
    return conv_id


async def orders(world: World) -> list[Order]:
    async with world.sessions() as s:
        return list(await s.scalars(select(Order)))


async def test_the_prompt_has_the_menu_and_the_note_has_the_order(world: World) -> None:
    agent = await world.agent()
    text = await RESTAURANT.prompt(agent.config, world.store(agent))

    assert "# Taking a pickup order" in text
    assert "Opening hours: Mon 11:00–22:00" in text
    assert "- Chicken Biryani [chk-biryani] ৳450 (popular, spicy): Basmati rice" in text
    assert "- Mango Lassi [mango-lassi] ৳150 (vegetarian) NOT AVAILABLE" in text
    assert RESTAURANT.note(AGENT, {}) == ""
    assert "2 × Chicken Biryani, 1 × Borhani, total ৳1,030" in RESTAURANT.note(
        AGENT, {"cart": CART}
    )


async def test_python_adds_up_the_order_and_checks_every_dish(world: World) -> None:
    agent = await world.agent()
    ctx = make_ctx(world.store(agent))

    def order(*lines: tuple[str, int]) -> UpdateOrderInput:
        return UpdateOrderInput.model_validate(
            {"lines": [{"code": c, "quantity": q} for c, q in lines]}
        )

    result = await update(ctx, order(("chk-biryani", 1), ("borhani", 1), ("chk-biryani", 1)))
    assert result.ok and result.data["total"] == "৳1,030"  # the same dish twice is added up
    assert result.state == {"cart": CART}

    unknown = await update(ctx, order(("pizza", 1)))
    assert not unknown.ok and "Not on the menu: pizza" in unknown.data["error"]
    sold_out = await update(ctx, order(("mango-lassi", 1)))
    assert not sold_out.ok and "Mango Lassi" in sold_out.data["error"]

    closed_today = AGENT.model_copy(update={"hours": WeeklyHours.model_validate({"tue": None})})
    closed = await update(make_ctx(world.store(agent), agent=closed_today), order(("borhani", 1)))
    assert not closed.ok and "closed today" in closed.data["error"]


async def test_place_order_asks_for_what_is_missing_or_wrong_first(world: World) -> None:
    agent = await world.agent()
    store = world.store(agent)

    no_cart = await place_order(make_ctx(store), PlaceOrderInput.model_validate(DETAILS))
    assert not no_cart.ok and "update_order first" in no_cart.data["error"]

    ctx = make_ctx(store, state={"cart": CART})
    just_a_time = await place_order(ctx, PlaceOrderInput(time="7:30 pm"))
    assert just_a_time.data["still_needed"] == ["name", "phone"]
    assert just_a_time.data["next"] == "Ask for the name, one question at a time."

    open_till_10 = make_ctx(store, state={"cart": CART}, agent=agent.config)  # Tue 11:00–22:00
    too_late = await place_order(
        open_till_10, PlaceOrderInput.model_validate(DETAILS | {"time": "11:30 pm"})
    )
    assert "outside our hours" in too_late.data["problems"]["time"]

    all_there = await place_order(ctx, PlaceOrderInput.model_validate(DETAILS))
    assert all_there.data["read_back"] == (
        "2 × Chicken Biryani, 1 × Borhani, total ৳1,030; pickup today at 7:30 pm; "
        "name Kayes; phone 01712345678"
    )  # no yes yet, so it's read back first
    assert await orders(world) == []


async def test_a_whole_order_is_saved_only_at_the_yes(world: World) -> None:
    """Three turns: the order and details, a "maybe", then a yes."""
    agent = await world.agent()
    world.decider.script = [
        {"read_back": 0.1, "said_yes": 0.1},  # first call: nothing read back yet
        {"read_back": 0.95, "said_yes": 0.3},  # "hmm, maybe"
        YES,
    ]
    order = [
        ToolCall("c1", "update_order", {"lines": LINES}),
        ToolCall("c2", "place_order", DETAILS),
    ]
    conv = await world.say(agent, None, [*order, READ_BACK], "two biryanis, 7:30, Kayes, 0171…")
    assert "Read this back and ask for a clear yes" in await world.tool_said(conv)

    await world.say(agent, conv, [ToolCall("c3", "place_order", DETAILS), READ_BACK], "hmm, maybe")
    assert await orders(world) == []

    await world.say(agent, conv, [ToolCall("c4", "place_order", DETAILS), "Done! 101."], "yes")
    [saved] = await orders(world)
    assert (saved.workspace_id, saved.conversation_id) == (DEV_WORKSPACE_ID, conv)
    assert (saved.number, saved.total, saved.name, saved.phone) == (
        101,
        103000,
        "Kayes",
        "01712345678",
    )
    assert saved.pickup_at.isoformat() == "2026-09-29T13:30:00+00:00"  # 7:30 pm in Dhaka
    assert (await world.conversation(conv)).state["cart"] == {}
    # Jev saw the read-back and the yes
    assert (
        world.decider.asked[-1]["agent_last_message"],
        world.decider.asked[-1]["visitor_reply"],
    ) == (
        READ_BACK,
        "yes",
    )


async def test_the_owner_edits_the_menu_and_moves_orders_along(
    app: FastAPI, client: AsyncClient
) -> None:
    conv_id = await place_an_order(app, client)

    [order] = (await client.get("/api/orders")).json()
    assert (order["number"], order["total"], order["name"]) == (101, 103000, "Kayes")
    assert order["conversation_id"] == conv_id and order["status"] == "new"
    assert (await client.get("/api/orders", params={"q": "kay"})).json()[0]["id"] == order["id"]
    assert (await client.get("/api/orders", params={"status": "ready"})).json() == []

    ready = await client.patch(f"/api/orders/{order['id']}", json={"status": "ready"})
    assert ready.json()["status"] == "ready"
    back = await client.patch(f"/api/orders/{order['id']}", json={"status": "new"})
    assert back.status_code == 409 and back.json()["error"]["code"] == "invalid_transition"

    dish = {"code": "dal", "name": "Dal", "price": 8000, "category": "Curry"}
    assert (await client.put("/api/menu", json=[dish])).status_code == 200
    assert [d["code"] for d in (await client.get("/api/menu")).json()] == ["dal"]
    assert (await client.get("/api/doctors")).status_code == 404  # a restaurant has none


async def test_the_menu_comes_from_a_csv(client: AsyncClient) -> None:
    csv = "Item,Price,Category,Tags\nDal,80,Curry,vegetarian; mild\n"
    bad = await client.post("/api/menu/import", json={"csv": csv})
    assert bad.status_code == 422  # no "name" column: "Item" isn't it

    await client.post("/api/menu/import", json={"csv": csv.replace("Item", "Name")})
    [dal] = (await client.get("/api/menu")).json()
    assert (dal["code"], dal["price"], dal["tags"]) == ("dal", 8000, ["vegetarian", "mild"])


async def ordered(world: World) -> tuple[str, list[Order]]:
    """An order placed in a new conversation, after a yes: (the conversation, its orders)."""
    agent = await world.agent()
    world.decider.script = [YES]
    order = [
        ToolCall("c1", "update_order", {"lines": LINES}),
        ToolCall("c2", "place_order", DETAILS),
        "Done!",
    ]
    conv = await world.say(agent, None, order, "two biryanis, 7:30, Kayes, 0171…, yes")
    return conv, await orders(world)


async def test_the_order_from_this_chat_is_tracked_changed_and_cancelled_with_no_code(
    world: World,
) -> None:
    agent = await world.agent()
    conv, _ = await ordered(world)
    assert '"order": "O-101"' in await world.tool_said(conv)  # the code, not #101

    def call(tool: str, args: dict[str, str] | None = None) -> list[object]:
        return [ToolCall("t", tool, args or {}), "…"]

    await world.say(agent, conv, call("track_order"), "where's my order?")
    tracked = await world.tool_said(conv)
    assert "O-101" in tracked and "the kitchen has it" in tracked and "7:30 pm" in tracked

    world.decider.script = [NO, YES]
    await world.say(agent, conv, call("change_order", {"time": "8 pm"}), "make it 8 pm")
    assert "Change order O-101: time: 7:30 pm → 8:00 pm" in await world.tool_said(conv)
    await world.say(agent, conv, call("change_order", {"time": "8 pm"}), "yes")
    [changed] = await orders(world)
    assert changed.pickup_at.isoformat() == "2026-09-29T14:00:00+00:00"  # 8 pm in Dhaka

    world.decider.script = [NO, YES]
    await world.say(agent, conv, call("cancel_order"), "cancel it please")
    assert (await orders(world))[0].status == "new"  # read back first
    await world.say(agent, conv, call("cancel_order"), "yes")
    assert (await orders(world))[0].status == "cancelled"


async def test_another_chat_needs_the_code_and_the_phone(world: World) -> None:
    agent = await world.agent()
    await ordered(world)

    def track(code: str, phone: str) -> list[object]:
        return [ToolCall("t", "track_order", {"code": code, "phone": phone}), "…"]

    no_code = await world.say(agent, None, track("", ""))
    assert "Ask for the order code" in await world.tool_said(no_code)
    wrong_phone = await world.say(agent, None, track("O-101", "01999999999"))
    wrong_code = await world.say(agent, None, track("O-109", "01712345678"))
    assert await world.tool_said(wrong_phone) == await world.tool_said(wrong_code)
    right = await world.say(agent, None, track("o 101", "01712345678"))
    assert "the kitchen has it" in await world.tool_said(right)


async def test_a_ready_order_cannot_be_changed_or_cancelled(world: World) -> None:
    agent = await world.agent()
    conv, [order] = await ordered(world)
    await world.store(agent).set_status(Order, order.id, "ready")

    await world.say(agent, conv, [ToolCall("t", "cancel_order", {}), "…"])
    assert "can't be cancelled now" in await world.tool_said(conv)
    await world.say(agent, conv, [ToolCall("t", "change_order", {"note": "extra spicy"}), "…"])
    assert "can't be changed now" in await world.tool_said(conv)


async def bookings(world: World) -> list[Booking]:
    async with world.sessions() as s:
        return list(await s.scalars(select(Booking)))


def book(details: dict[str, object]) -> list[object]:
    return [ToolCall("b", "book_table", details), "…"]


async def test_a_booking_is_saved_only_after_a_yes(world: World) -> None:
    agent = await world.agent()
    world.decider.script = [NO, {"read_back": 0.95, "said_yes": 0.3}, YES]
    conv = await world.say(agent, None, book(TABLE), "table for 4 tomorrow at 8 pm")
    assert "Table for 4 on Wed 30 Sep, 8:00 pm; name Kayes; phone 01712345678" in (
        await world.tool_said(conv)
    )

    await world.say(agent, conv, book(TABLE), "hmm, maybe")
    assert await bookings(world) == []

    await world.say(agent, conv, book(TABLE), "yes")
    [saved] = await bookings(world)
    assert (saved.code, saved.people, saved.status, saved.at) == ("T-101", 4, "new", WED_8PM)
    assert (saved.workspace_id, saved.conversation_id) == (DEV_WORKSPACE_ID, conv)
    assert "T-101" in await world.tool_said(conv)


async def test_a_full_slot_is_refused_with_the_nearest_free_times(world: World) -> None:
    agent = await world.agent()  # 40 seats, a table kept 90 minutes, open till 22:00
    store = world.store(agent)
    await store.add(Booking, name="Big party", phone="01700000000", at=WED_8PM, people=38)
    ctx = make_ctx(store, agent=agent.config)

    full = await book_table(ctx, BookTableInput(people=4, time="tomorrow at 8 pm"))
    assert full.data["error"] == (
        "We're full for 4 at Wed 30 Sep, 8:00 pm. "
        "Free times nearby: 6:00 pm, 6:30 pm, 9:30 pm. Offer them."
    )  # said before the name and phone are asked
    two = await book_table(ctx, BookTableInput(people=2, time="tomorrow at 8 pm"))
    assert two.data["still_needed"] == ["name", "phone"]  # two more still fit


def test_the_seats_are_counted_when_each_table_sits_down() -> None:
    seven, nine = WED_8PM.replace(hour=13), WED_8PM.replace(hour=15)
    taken = [(seven, 20), (nine, 20)]
    # At 8 pm the 7 pm table is still there, and at 9 pm only the 9 pm one: 24 at most
    assert has_room(taken, WED_8PM, 4, seats=40, minutes=90)
    assert not has_room(taken, WED_8PM, 4, seats=40, minutes=150)  # all three at 9 pm


async def test_a_time_outside_the_hours_or_a_party_too_big_is_refused(world: World) -> None:
    agent = await world.agent()
    ctx = make_ctx(world.store(agent), agent=agent.config)

    late = await book_table(ctx, BookTableInput(people=4, time="tomorrow at 11 pm"))
    assert "outside our hours" in late.data["problems"]["time"]
    big = await book_table(ctx, BookTableInput(people=13, time="tomorrow at 8 pm"))
    assert "up to 12 people" in big.data["problems"]["people"]
    nothing = await book_table(ctx, BookTableInput())
    assert nothing.data["still_needed"] == ["people", "time", "name", "phone"]


async def test_a_booking_is_cancelled_with_its_code_and_name(world: World) -> None:
    agent = await world.agent()
    store = world.store(agent)
    await store.add(Booking, name="Kayes", phone="01712345678", at=WED_8PM, people=4)

    def cancel(code: str, name: str) -> list[object]:
        return [ToolCall("c", "cancel_booking", {"code": code, "name": name}), "…"]

    world.decider.script = [NO, YES]
    wrong_name = await world.say(agent, None, cancel("T-101", "Someone Else"))
    wrong_code = await world.say(agent, None, cancel("T-109", "Kayes"))
    assert await world.tool_said(wrong_name) == await world.tool_said(wrong_code)

    conv = await world.say(agent, None, cancel("t 101", "kayes"))
    assert "Cancel booking T-101: table for 4 on Wed 30 Sep, 8:00 pm" in (
        await world.tool_said(conv)
    )
    assert (await bookings(world))[0].status == "new"
    await world.say(agent, conv, cancel("T-101", "Kayes"), "yes")
    assert (await bookings(world))[0].status == "cancelled"


async def test_with_bookings_off_there_is_no_tool_and_no_prompt_line(world: World) -> None:
    agent = await world.agent()
    on = agent.config
    off = on.model_copy(update={"takes_bookings": False})
    assert "book_table" in [t.name for t in RESTAURANT.own_tools(on)]
    assert "# Table bookings" in await RESTAURANT.prompt(on, world.store(agent))
    assert "book_table" not in [t.name for t in RESTAURANT.own_tools(off)]
    assert "book_table" not in await RESTAURANT.prompt(off, world.store(agent))


async def test_another_workspace_cannot_see_the_bookings(world: World) -> None:
    agent = await world.agent()
    await world.store(agent).add(Booking, name="Kayes", phone="0171", at=WED_8PM, people=4)
    other = agent.desk.store(world.sessions, "someone-else", world.clock)
    assert await other.booking(101) is None
    assert await other.seats_taken(WED_8PM) == []  # rule 6: workspace_id on every query


async def test_the_owner_sees_and_moves_bookings(app: FastAPI, client: AsyncClient) -> None:
    runtime = app.state.services.conversations.runtime
    runtime.llm.script, runtime.decider.script = book(TABLE), [YES]
    chat = await client.post(
        f"/api/chat/{DEV_KEY}", json={"visitor_id": "test-visitor", "message": "a table, yes"}
    )
    conv_id = chat.text.split('"conversation_id": "')[1].split('"')[0]

    [booking] = (await client.get("/api/bookings", params={"q": "T-101"})).json()
    assert (booking["code"], booking["people"], booking["status"]) == ("T-101", 4, "new")
    confirmed = await client.patch(f"/api/bookings/{booking['id']}", json={"status": "confirmed"})
    assert confirmed.json()["status"] == "confirmed"
    skipped = await client.patch(f"/api/bookings/{booking['id']}", json={"status": "done"})
    assert skipped.status_code == 409
    # The visitor hears it in their chat, as the team's message (D66)
    team = await client.get(f"/api/chat/{DEV_KEY}/{conv_id}/team")
    assert team.json()["replies"] == ["✅ Your table for 4 is confirmed (T-101). See you then!"]


async def test_an_order_marked_ready_tells_the_customer_in_the_chat(
    app: FastAPI, client: AsyncClient
) -> None:
    conv_id = await place_an_order(app, client)
    [order] = (await client.get("/api/orders")).json()
    team = f"/api/chat/{DEV_KEY}/{conv_id}/team"
    assert (await client.get(team)).json()["replies"] == []

    await client.patch(f"/api/orders/{order['id']}", json={"status": "ready"})
    assert (await client.get(team)).json()["replies"] == ["🍽️ Your order O-101 is ready to pick up."]
    # A pickup isn't sent out for delivery; it's collected, and that says nothing more
    out = await client.patch(f"/api/orders/{order['id']}", json={"status": "out_for_delivery"})
    assert out.status_code == 409
    await client.patch(f"/api/orders/{order['id']}", json={"status": "collected"})
    assert len((await client.get(team)).json()["replies"]) == 1


def test_a_delivery_goes_out_and_is_delivered_but_never_collected() -> None:
    order = Order(status="ready", address="House 1, Road 2", number=101)
    with pytest.raises(ValueError):
        order.move("collected", WED_8PM)
    order.move("out_for_delivery", WED_8PM)
    assert order.news() == "🛵 Your order O-101 is on its way."
    order.move("delivered", WED_8PM)
