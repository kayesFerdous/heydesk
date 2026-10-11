"""The restaurant desk: its menu in the prompt, an order Python adds up, saved only after a
read-back and a yes, and the owner's menu and orders routes."""

from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select

from heydesk.desks.restaurant import RESTAURANT
from heydesk.desks.restaurant.models import Order
from heydesk.desks.restaurant.tools import PlaceOrderInput, UpdateOrderInput, place_order
from heydesk.desks.restaurant.tools import update_order as update
from heydesk.ports.llm import ToolCall
from heydesk.runtime.schedule import WeeklyHours
from heydesk.services.agents import DEV_WORKSPACE_ID
from tests.conftest import AGENT, CART, DEV_KEY, World, make_ctx

LINES = [{"code": "chk-biryani", "quantity": 2}, {"code": "borhani", "quantity": 1}]
DETAILS = {"time": "7:30 pm", "name": "Kayes", "phone": "01712345678"}
READ_BACK = "2 Chicken Biryani and 1 Borhani, ৳1,030, at 7:30 pm for Kayes. Shall I place it?"
YES = {"read_back": 0.95, "said_yes": 0.95}


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
    first = await client.post(f"/api/chat/{DEV_KEY}", json={"message": "two biryanis…"})
    conv_id = first.text.split('"conversation_id": "')[1].split('"')[0]
    await client.post(f"/api/chat/{DEV_KEY}", json={"message": "yes", "conversation_id": conv_id})
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
