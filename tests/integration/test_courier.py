"""The courier (D112): only a confirmed order is booked, once, with the cash still owed, and the
fake courier's last word makes it delivered; the buyer hears each move in their chat."""

from heydesk.desks.shop.tools import FindOrderInput, track_order
from heydesk.services.agents import DEV_WORKSPACE_ID
from tests.conftest import World, make_ctx
from tests.integration.test_shop import KURTI, buy, orders


async def test_a_confirmed_order_is_booked_followed_and_delivered(world: World) -> None:
    agent = await world.agent("shop")
    await buy(world, agent, KURTI)
    courier = world.services.courier
    assert courier is not None
    await courier.sync(DEV_WORKSPACE_ID)
    assert (await orders(world))[0].parcel_id == ""  # new: the shop calls first
    await world.store(agent).move_order((await orders(world))[0].id, "confirmed")
    await courier.sync(DEV_WORKSPACE_ID)
    await courier.sync(DEV_WORKSPACE_ID)  # booked once
    [order] = await orders(world)
    assert order.status == "shipped" and order.parcel_id.startswith("FAKE-O-101-")
    assert order.courier_status == "Pickup requested"
    tracked = await track_order(
        make_ctx(world.store(agent), agent=agent.config),
        FindOrderInput(code="O-101", phone="01712345678"),
    )
    assert tracked.data["courier_says"] == "Pickup requested"

    world.clock.advance(minutes=5)
    await courier.sync(DEV_WORKSPACE_ID)
    assert (await orders(world))[0].courier_status == "In transit"
    world.clock.advance(minutes=5)
    await courier.sync(DEV_WORKSPACE_ID)
    [order] = await orders(world)
    assert (order.status, order.courier_status) == ("delivered", "Delivered")
    assert order.conversation_id
    told = [m["content"] for m in (await world.conversation(order.conversation_id)).messages]
    assert any("is with the courier" in str(m) for m in told)
    assert any("was delivered" in str(m) for m in told)
