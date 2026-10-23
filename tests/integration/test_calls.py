"""Calls to confirm shop orders (D88, D89): off for now, since the shop takes no voice (D108).
The code stays, so these tests turn the shop's voice back on (`shop_voice`) to keep it working:
off until the shop turns them on; then each new
order is queued and called in turn, inside the calling hours, on the buyer's `/phone` page; a
missed call is tried again and then the owner is told; only a yes on the call confirms the
order. And the owner's routes for the queue."""

from datetime import timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select

from heydesk.desks.shop import ShopDesk
from heydesk.desks.shop.models import ShopOrder
from heydesk.errors import Conflict, HeyDeskError, NotFound
from heydesk.ports.llm import ToolCall
from heydesk.services import Services
from heydesk.services.agents import DEV_WORKSPACE_ID, LiveAgent
from tests.conftest import SEEDS, World
from tests.integration.test_shop import DETAILS, KURTI, READ_BACK

MOBILE = "01712345678"  # the one in DETAILS


@pytest.fixture
def shop_voice(monkeypatch: pytest.MonkeyPatch) -> None:
    """The shop takes voice again, as it did before D108, for the tests of the calls' code."""
    monkeypatch.setattr(ShopDesk, "takes_voice", True)


async def shop_with_calls(services: Services, **settings: Any) -> LiveAgent:
    """The shop dev agent with calls on, at once unless `settings` say otherwise."""
    await services.agents.ensure_dev_agent("shop", SEEDS / "shop.yaml")
    calls = {"enabled": True, "wait_minutes": 0, **settings}
    await services.agents.update(DEV_WORKSPACE_ID, {"calls": calls})
    return await services.agents.by_key("dev-shop")


async def order(services: Services, agent: LiveAgent, phone: str = MOBILE) -> ShopOrder:
    """A kurti ordered over two turns, with a yes; returns the order."""
    runtime = services.conversations.runtime
    details = {**DETAILS, "phone": phone}
    runtime.llm.script = [
        ToolCall("c1", "update_cart", {"lines": [KURTI]}),
        ToolCall("c2", "place_order", details),
        READ_BACK,
        ToolCall("c3", "place_order", details),
        "Done.",
    ]
    turn = await services.conversations.start_turn(agent, None, "a kurti", "web", "web:buyer")
    [_ async for _ in turn.events]
    turn = await services.conversations.start_turn(
        agent, turn.conversation_id, "yes", "web", "web:buyer"
    )
    [_ async for _ in turn.events]
    async with services.conversations.sessions() as s:
        found = await s.scalar(select(ShopOrder).order_by(ShopOrder.number.desc()).limit(1))
    assert found is not None
    return found


async def status_of(world: World, order_id: str) -> str:
    async with world.sessions() as s:
        found = await s.get(ShopOrder, order_id)
    assert found is not None
    return found.status


async def test_a_shop_order_queues_no_call_since_the_shop_takes_no_voice(world: World) -> None:
    agent = await shop_with_calls(world.services)  # even with calls on (D108)
    placed = await order(world.services, agent)
    assert await world.services.calls.find(DEV_WORKSPACE_ID) == []
    with pytest.raises(Conflict, match="a shop takes no calls"):
        await world.services.calls.call_order(DEV_WORKSPACE_ID, placed.id)


async def test_nothing_is_queued_while_calls_are_off(world: World, shop_voice: None) -> None:
    agent = await world.agent("shop")
    await order(world.services, agent)
    assert await world.services.calls.find(DEV_WORKSPACE_ID) == []
    with pytest.raises(Conflict, match="Calls are off"):
        found = await world.store(agent).find(ShopOrder)
        await world.services.calls.call_order(DEV_WORKSPACE_ID, found[0].id)


async def test_production_queues_no_calls_since_it_serves_no_phone_page(
    world: World, monkeypatch: pytest.MonkeyPatch, shop_voice: None
) -> None:
    monkeypatch.setattr(world.services.calls.settings, "APP_ENV", "production")
    agent = await shop_with_calls(world.services)
    placed = await order(world.services, agent)
    assert await world.services.calls.find(DEV_WORKSPACE_ID) == []
    with pytest.raises(Conflict, match="/phone"):
        await world.services.calls.call_order(DEV_WORKSPACE_ID, placed.id)


async def test_a_new_order_rings_the_buyer_and_only_a_yes_on_the_call_confirms_it(
    world: World, shop_voice: None
) -> None:
    calls = world.services.calls
    agent = await shop_with_calls(world.services)
    phone = calls.listen(calls.number("+880 1712-345678"))  # the /phone page
    placed = await order(world.services, agent)
    [queued] = await calls.find(DEV_WORKSPACE_ID)
    assert (queued.status, queued.code, queued.phone) == ("queued", placed.code, MOBILE)

    await calls.tick()
    ring = phone.get_nowait()
    assert ring == {"call": queued.id, "business": "Nakshi Threads", "order": "O-101"}

    answered = await calls.answer(ring["call"])
    assert "Nakshi Threads calling about your order O-101, total ৳1,010" in answered.said
    call = await world.conversation(answered.conversation_id)
    assert (call.channel, call.messages[0]["content"]) == ("voice", answered.said)
    [talking] = await calls.find(DEV_WORKSPACE_ID)
    assert (talking.status, talking.conversation_id) == ("talking", call.id)

    confirm = ToolCall("c", "confirm_order", {})
    await world.say(agent, call.id, ["Shall I confirm it?"], "hmm, wait")  # not a yes
    assert await status_of(world, placed.id) == "new"

    await world.say(agent, call.id, [confirm, "Confirmed, thank you. Goodbye!"], "yes")
    assert await status_of(world, placed.id) == "confirmed"
    assert (await world.conversation(call.id)).state["calling"]["over"]  # it hangs up

    await calls.ended(answered.call_id)
    [done] = await calls.find(DEV_WORKSPACE_ID, "done")
    assert (done.status, done.outcome) == ("done", "confirmed")
    with pytest.raises(NotFound):
        await calls.answer(ring["call"])  # it's over


async def test_the_queue_waits_for_the_hours_and_a_free_line_and_call_now_goes_first(
    world: World, shop_voice: None
) -> None:
    calls = world.services.calls
    agent = await shop_with_calls(world.services, start="15:00")  # it's 14:00 in Dhaka
    phones = [calls.listen(MOBILE), calls.listen("01812345678")]
    first = await order(world.services, agent)
    second = await order(world.services, agent, phone="01812345678")

    await calls.tick()
    assert all(c.status == "queued" for c in await calls.find(DEV_WORKSPACE_ID))  # too early
    waiting = await calls.find(DEV_WORKSPACE_ID)
    assert [c.order_id for c in waiting] == [first.id, second.id]  # the oldest first

    await calls.call_now(DEV_WORKSPACE_ID, waiting[1].id)
    assert [c.order_id for c in await calls.find(DEV_WORKSPACE_ID)] == [second.id, first.id]

    world.clock.advance(hours=1)
    await calls.tick()
    assert phones[1].get_nowait()["order"] == second.code
    assert phones[0].empty()  # one line: the next waits for it
    assert [c.status for c in await calls.find(DEV_WORKSPACE_ID)] == ["ringing", "queued"]


async def test_a_missed_call_is_tried_again_then_the_owner_is_told(
    world: World, shop_voice: None
) -> None:
    calls = world.services.calls
    agent = await shop_with_calls(world.services, tries=2, retry_minutes=30)
    placed = await order(world.services, agent)

    await calls.tick()  # no page open: the phone is off
    [call] = await calls.find(DEV_WORKSPACE_ID)
    assert (call.status, call.tries, call.next_try_at) == (
        "queued",
        1,
        world.clock.now + timedelta(minutes=30),
    )

    phone = calls.listen(MOBILE)
    world.clock.advance(minutes=31)
    await calls.tick()
    assert phone.get_nowait()["call"] == call.id
    world.clock.advance(seconds=world.services.calls.settings.RING_SECONDS + 1)
    await calls.tick()  # rang out
    [done] = await calls.find(DEV_WORKSPACE_ID, "done")
    assert (done.outcome, done.tries) == ("no_answer", 2)
    assert await status_of(world, placed.id) == "new"  # the owner calls them


async def test_a_declined_call_waits_and_an_order_settled_first_isnt_called(
    world: World, shop_voice: None
) -> None:
    calls = world.services.calls
    agent = await shop_with_calls(world.services)
    phone = calls.listen(MOBILE)
    placed = await order(world.services, agent)
    await calls.tick()
    await calls.missed(phone.get_nowait()["call"])  # Decline
    [call] = await calls.find(DEV_WORKSPACE_ID)
    assert call.status == "queued"

    await world.store(agent).move_order(placed.id, "confirmed")  # the owner phoned them
    world.clock.advance(minutes=31)
    await calls.tick()
    [done] = await calls.find(DEV_WORKSPACE_ID, "done")
    assert done.outcome == "skipped"
    assert phone.empty()


async def test_confirm_order_only_works_on_the_shops_call(world: World) -> None:
    agent = await world.agent("shop")
    placed = await order(world.services, agent)
    confirm = ToolCall("c", "confirm_order", {})
    await world.say(agent, placed.conversation_id, [confirm, "The shop will call."], "confirm it")
    assert "Only the shop's call confirms an order" in await world.tool_said(
        str(placed.conversation_id)
    )
    with pytest.raises(HeyDeskError, match="11 digits"):
        world.services.calls.number("12345")


async def test_the_owner_sees_and_steers_the_queue(
    app: FastAPI, client: AsyncClient, shop_voice: None
) -> None:
    services: Services = app.state.services
    agent = await shop_with_calls(services, wait_minutes=60)
    placed = await order(services, agent)

    [waiting] = (await client.get("/api/calls")).json()
    assert (waiting["code"], waiting["status"], waiting["place"]) == ("O-101", "queued", 1)
    assert (await client.get("/api/calls", params={"order_id": placed.id})).json()[0]["id"] == (
        waiting["id"]
    )
    again = await client.post("/api/calls", json={"order_id": placed.id})
    assert again.status_code == 409  # it has one waiting

    moved = await client.post(f"/api/calls/{waiting['id']}/now")
    assert moved.json()["next_try_at"] < waiting["next_try_at"]
    stopped = await client.delete(f"/api/calls/{waiting['id']}")
    assert (stopped.json()["status"], stopped.json()["outcome"]) == ("done", "stopped")
    assert (await client.get("/api/calls", params={"view": "done"})).json()[0]["outcome"] == (
        "stopped"
    )

    called = await client.post("/api/calls", json={"order_id": placed.id})
    assert (called.status_code, called.json()["status"]) == (201, "queued")
