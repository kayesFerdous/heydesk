"""Plans and paying HeyDesk for one (D106)."""

from datetime import timedelta

import pytest
from fastapi import FastAPI

from heydesk.adapters.payments.fake import FakePayments
from heydesk.errors import VisitorLimited
from heydesk.models import Turn, Workspace
from heydesk.ports.payments import Paid
from heydesk.services.agents import DEV_WORKSPACE_ID
from heydesk.services.billing import GRACE_DAYS, PLANS, TRIAL_DAYS, BillingService
from tests.conftest import FIXED_NOW, World, make_test_settings, new_business


async def test_a_new_business_is_on_the_trial(app: FastAPI) -> None:
    owner = await new_business(app)
    body = (await owner.get("/api/billing")).json()
    assert body["plan"] == "trial" and body["active"] is True
    assert body["replies"] == PLANS["trial"].replies and body["replies_used"] == 0
    assert body["paid_until"].startswith(str((FIXED_NOW + timedelta(days=TRIAL_DAYS)).date()))
    starter = next(p for p in body["plans"] if p["key"] == "starter")
    assert starter["price"] == 249_000 and starter["year_price"] == 2_490_000
    # Paying online needs our store, which tests don't have
    assert (await owner.post("/api/billing/checkout", json={"plan": "growth"})).status_code == 400


async def test_the_trial_has_one_moderator(app: FastAPI) -> None:
    owner = await new_business(app)
    assert (await owner.post("/api/team/members", json={"email": "a@example.com"})).is_success
    second = await owner.post("/api/team/members", json={"email": "b@example.com"})
    assert second.status_code == 409 and "plan" in second.json()["error"]["message"]


async def test_the_agent_stops_when_the_plan_ends_or_its_replies_run_out(world: World) -> None:
    agent = await world.agent()
    billing = world.services.billing
    visitors = world.services.visitors

    # A paid plan keeps answering for the grace days after its last paid day, then stops
    await billing.set_plan(DEV_WORKSPACE_ID, "starter", FIXED_NOW - timedelta(days=1))
    await visitors.admit(agent, "web:a")
    world.clock.advance(days=GRACE_DAYS)
    with pytest.raises(VisitorLimited):
        await visitors.admit(agent, "web:a")
    assert (await billing.billing(DEV_WORKSPACE_ID)).active is False

    # A trial's replies: the 200th in 30 days is the last
    await billing.set_plan(DEV_WORKSPACE_ID, "trial", world.clock.now + timedelta(days=7))
    conv = await world.say(agent, None, ["Hi!"])  # one turn, saved in `turns`
    async with world.sessions() as s, s.begin():
        s.add_all(
            Turn(
                workspace_id=DEV_WORKSPACE_ID,
                conversation_id=conv,
                channel="web",
                llm="fake",
                at=world.clock.now,
                total_ms=1,
                first_output_ms=1,
                input_tokens=1,
                output_tokens=1,
                cost_micros=0,
                tools=[],
                handed_off=False,
                interrupted=False,
            )
            for _ in range(PLANS["trial"].replies - 1)
        )
    with pytest.raises(VisitorLimited):
        await visitors.admit(agent, "web:b")
    world.clock.advance(days=31)  # older replies stop counting, but the trial has ended too
    with pytest.raises(VisitorLimited):
        await visitors.admit(agent, "web:b")


async def test_a_paid_plan_starts_once_and_only_for_the_right_amount(world: World) -> None:
    gateway = FakePayments()
    settings = make_test_settings().model_copy(update={"BILLING_STORE_ID": "heydesk"})
    billing = BillingService(world.sessions, gateway, settings, world.clock)
    await billing.set_plan(DEV_WORKSPACE_ID, "trial", FIXED_NOW + timedelta(days=5))

    url = await billing.checkout(DEV_WORKSPACE_ID, "growth", 12)
    [(account, checkout)] = gateway.started
    assert account.store_id == "heydesk" and url.endswith(checkout.order_id)
    assert checkout.amount == PLANS["growth"].cost(12) == 5_990_000

    # Less than the price: nothing changes
    gateway.paid["short"] = Paid(checkout.order_id, 100, "BDT", "bank-1")
    assert (await billing.settle(checkout.order_id, "short")).status == "unpaid"  # type: ignore[union-attr]

    gateway.paid["ok"] = Paid(checkout.order_id, checkout.amount, "BDT", "bank-2")
    assert (await billing.settle(checkout.order_id, "ok")).status == "paid"  # type: ignore[union-attr]
    await billing.settle(checkout.order_id, "ok")  # the gateway's notice too: no second year
    async with world.sessions() as s:
        ws = await s.get(Workspace, DEV_WORKSPACE_ID)
    assert ws is not None and ws.plan == "growth"
    assert ws.paid_until == FIXED_NOW + timedelta(days=365)  # a trial's days aren't added
