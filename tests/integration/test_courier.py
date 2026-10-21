"""A shop's courier (D90): only confirmed orders are booked, once, with the cash still owed;
a refused booking stays confirmed and is tried again; the courier's last word makes the order
delivered or returned; a wrong key is refused."""

import asyncio

from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from heydesk.desks.shop.models import ShopOrder
from heydesk.ports.courier import Booked, CourierAccount, CourierError, Parcel, Tracked
from heydesk.services.agents import DEV_WORKSPACE_ID
from tests.conftest import SEEDS, World
from tests.integration.test_shop import KURTI, PANJABIS, buy, orders

STEADFAST = CourierAccount("steadfast", "key-1", "secret-1")


class FakeCourier:
    """A courier: the parcels booked with it, and what it says about each."""

    def __init__(self) -> None:
        self.parcels: dict[str, Parcel] = {}
        self.says: dict[str, Tracked] = {}
        self.down = ""  # a problem for every booking

    async def check(self, account: CourierAccount) -> int | None:
        if account.secret != "secret-1":
            raise CourierError("Steadfast refused the keys: check the API key and secret key.")
        return None

    async def book(self, account: CourierAccount, parcel: Parcel) -> Booked:
        if self.down:
            raise CourierError(self.down)
        parcel_id = str(1000 + len(self.parcels))
        self.parcels[parcel_id] = parcel
        self.says[parcel_id] = Tracked("in_review", None)
        return Booked(parcel_id)

    async def status(self, account: CourierAccount, parcel_id: str) -> Tracked:
        return self.says[parcel_id]


async def confirmed(
    sessions: async_sessionmaker[AsyncSession], number: int, status: str = "confirmed"
) -> None:
    """A confirmed order for one Cotton Tupi to Mirpur, ৳360 with delivery."""
    line = {"code": "TP-1", "name": "Cotton Tupi", "size": "", "colour": "", "quantity": 1}
    async with sessions() as s, s.begin():
        s.add(
            ShopOrder(
                workspace_id=DEV_WORKSPACE_ID,
                number=number,
                status=status,
                name="Rahim",
                phone="01712345678",
                lines=[line | {"price": 30000}],
                subtotal=30000,
                delivery_charge=6000,
                total=36000,
                currency="BDT",
                zone="Mirpur",
                address="House 3, Road 4",
            )
        )


async def linked(world: World) -> FakeCourier:
    courier = FakeCourier()
    world.services.courier.apis = {"steadfast": courier}
    await world.services.courier.link(DEV_WORKSPACE_ID, STEADFAST)
    return courier


async def test_only_a_confirmed_order_is_booked_and_only_once(world: World) -> None:
    agent = await world.agent("shop")
    courier = await linked(world)
    await buy(world, agent, PANJABIS)
    await asyncio.gather(*world.services.courier.tasks)  # the turns' rounds

    assert courier.parcels == {}  # new: maybe a fake order, so no parcel yet

    [order] = await orders(world)
    await world.store(agent).move_order(order.id, "confirmed")
    await world.services.courier.sync(DEV_WORKSPACE_ID)
    await world.services.courier.sync(DEV_WORKSPACE_ID)
    [(parcel_id, parcel)] = courier.parcels.items()
    assert (parcel.code, parcel.phone, parcel.quantity) == (order.code, order.phone, 2)
    assert parcel.cod * 100 == order.total - order.paid  # whole taka, what the rider collects
    [order] = await orders(world)
    assert (order.status, order.parcel_id) == ("shipped", parcel_id)
    assert order.tracking == f"Steadfast {parcel_id}"
    assert order.courier_status == "in_review"


async def test_a_refused_booking_stays_confirmed_and_is_tried_again(world: World) -> None:
    agent = await world.agent("shop")
    courier = await linked(world)
    await buy(world, agent, KURTI)
    await asyncio.gather(*world.services.courier.tasks)  # the turns' rounds: nothing confirmed
    [order] = await orders(world)
    await world.store(agent).move_order(order.id, "confirmed")

    courier.down = "Steadfast answered 422. The recipient address is too short."
    link = await world.services.courier.sync(DEV_WORKSPACE_ID)
    assert link.problem == f"{order.code} wasn't booked with Steadfast: {courier.down}"
    assert (await orders(world))[0].status == "confirmed"

    courier.down = ""
    link = await world.services.courier.sync(DEV_WORKSPACE_ID)
    assert link.problem == ""
    assert (await orders(world))[0].status == "shipped"


async def test_the_couriers_last_word_ends_the_order(world: World) -> None:
    await world.agent("shop")
    courier = await linked(world)
    await confirmed(world.sessions, 101)
    await confirmed(world.sessions, 102)
    await world.services.courier.sync(DEV_WORKSPACE_ID)
    first, second = sorted(await orders(world), key=lambda o: o.number)
    courier.says[first.parcel_id] = Tracked("delivered", "delivered")
    courier.says[second.parcel_id] = Tracked("cancelled", "returned")
    await world.services.courier.sync(DEV_WORKSPACE_ID)
    first, second = sorted(await orders(world), key=lambda o: o.number)
    assert (first.status, first.courier_status) == ("delivered", "delivered")
    assert (second.status, second.courier_status) == ("returned", "cancelled")


async def test_a_wrong_key_is_refused_and_the_owners_confirm_books_it(
    app: FastAPI, client: AsyncClient
) -> None:
    await app.state.services.agents.ensure_dev_agent("shop", SEEDS / "shop.yaml")
    courier = FakeCourier()
    service = app.state.services.courier
    service.apis = {"steadfast": courier}
    body = {"kind": "steadfast", "key": "key-1", "secret": "wrong"}
    refused = await client.put("/api/agent/courier", json=body)
    assert refused.status_code == 422 and "refused the keys" in refused.text
    body["secret"] = "secret-1"
    assert (await client.put("/api/agent/courier", json=body)).json()["kind"] == "steadfast"
    assert "secret" not in (await client.get("/api/agent/courier")).json()

    # an order the owner confirms is booked at once, without waiting for the round
    await confirmed(app.state.sessions, 101, status="new")
    [order] = (await client.get("/api/shop/orders")).json()
    await client.patch(f"/api/shop/orders/{order['id']}", json={"status": "confirmed"})
    await asyncio.gather(*service.tasks)
    [parcel] = courier.parcels.values()
    assert (parcel.code, parcel.cod, parcel.address) == ("O-101", 360, "House 3, Road 4, Mirpur")
    booked = (await client.get(f"/api/shop/orders/{order['id']}")).json()
    assert (booked["status"], booked["parcel_id"]) == ("shipped", "1000")
