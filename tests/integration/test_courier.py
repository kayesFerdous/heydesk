"""The courier (D90, D112): with no courier linked, the fake one books only a confirmed order,
once, and its last word makes it delivered; the buyer hears each move in their chat. A linked
courier is checked when saved, books with the cash still owed, and a refused booking stays
confirmed and is tried again."""

import pytest

from heydesk.desks.shop.tools import FindOrderInput, track_order
from heydesk.errors import InvalidConfig
from heydesk.ports.courier import CourierAccount, CourierError, Parcel, Tracked
from heydesk.services.agents import DEV_WORKSPACE_ID
from tests.conftest import World, make_ctx
from tests.integration.test_shop import KURTI, buy, orders


async def test_a_confirmed_order_is_booked_followed_and_delivered(world: World) -> None:
    agent = await world.agent("shop")
    await buy(world, agent, KURTI)
    courier = world.services.courier
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


class Steadfast:
    """A linked courier that keeps what it was given."""

    def __init__(self) -> None:
        self.parcels: dict[str, Parcel] = {}
        self.down = ""  # a problem for every booking

    async def check(self, account: CourierAccount) -> int | None:
        if account.secret != "secret-1":
            raise CourierError("Steadfast refused the keys: check the API key and secret key.")
        return None

    async def book(self, account: CourierAccount, parcel: Parcel) -> str:
        if self.down:
            raise CourierError(self.down)
        self.parcels[parcel_id := str(1000 + len(self.parcels))] = parcel
        return parcel_id

    async def status(self, account: CourierAccount, parcel_id: str) -> Tracked:
        return Tracked("in_review", None)


async def test_a_linked_courier_is_checked_books_and_is_tried_again(world: World) -> None:
    agent = await world.agent("shop")
    courier, service = Steadfast(), world.services.courier
    service.apis["steadfast"] = courier
    with pytest.raises(InvalidConfig, match="refused the keys"):
        await service.link(DEV_WORKSPACE_ID, CourierAccount("steadfast", "key-1", "wrong"))
    await service.link(DEV_WORKSPACE_ID, CourierAccount("steadfast", "key-1", "secret-1"))

    await buy(world, agent, KURTI)
    [order] = await orders(world)
    await world.store(agent).move_order(order.id, "confirmed")
    courier.down = "Steadfast answered 422. The recipient address is too short."
    await service.sync(DEV_WORKSPACE_ID)
    link = await service.get(DEV_WORKSPACE_ID)
    assert link.problem == f"O-101 wasn't booked with Steadfast: {courier.down}"
    assert (await orders(world))[0].status == "confirmed"

    courier.down = ""
    await service.sync(DEV_WORKSPACE_ID)
    assert (await service.get(DEV_WORKSPACE_ID)).problem == ""
    [parcel] = courier.parcels.values()  # not the fake courier: the linked one
    assert parcel.code == "O-101" and parcel.cod * 100 == order.total - order.paid
    [order] = await orders(world)
    assert (order.status, order.parcel_id) == ("shipped", "1000")
