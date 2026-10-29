"""Plans and paying HeyDesk for one."""

from datetime import timedelta

import pytest
from fastapi import FastAPI

from heydesk.adapters.payments.fake import FakeBkash, FakePayments
from heydesk.errors import Conflict, VisitorLimited
from heydesk.models import Turn, Workspace
from heydesk.ports.payments import Gateways, Paid
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
    await visitors.count_turn(agent, "web:a")
    world.clock.advance(days=GRACE_DAYS)
    with pytest.raises(VisitorLimited):
        await visitors.count_turn(agent, "web:a")
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
        await visitors.count_turn(agent, "web:b")
    world.clock.advance(days=31)  # older replies stop counting, but the trial has ended too
    with pytest.raises(VisitorLimited):
        await visitors.count_turn(agent, "web:b")


async def test_a_paid_plan_starts_once_and_only_for_the_right_amount(world: World) -> None:
    gateway = FakePayments()
    settings = make_test_settings().model_copy(update={"BILLING_STORE_ID": "heydesk"})
    billing = BillingService(world.sessions, {"sslcommerz": gateway}, settings, world.clock)
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


async def test_a_plan_paid_by_bkash_starts_the_same_way(app: FastAPI, world: World) -> None:
    keys = {"BILLING_STORE_ID": "heydesk", "BILLING_BKASH_APP_KEY": "heydesk-key"}
    settings = make_test_settings().model_copy(update=keys)
    bkash = FakeBkash()
    gateways: Gateways = {"sslcommerz": FakePayments(), "bkash": bkash}
    billing = BillingService(world.sessions, gateways, settings, world.clock)
    assert (await billing.billing(DEV_WORKSPACE_ID)).gateways == ["sslcommerz", "bkash"]
    await billing.set_plan(DEV_WORKSPACE_ID, "starter", FIXED_NOW + timedelta(days=5))

    url = await billing.checkout(DEV_WORKSPACE_ID, "growth", 1, "bkash")
    [(account, checkout)] = bkash.started
    assert account.app_key == "heydesk-key" and url == f"https://bkash.test/{checkout.order_id}"
    assert checkout.back_url.endswith(f"/billing/{checkout.order_id}/bkash")

    bkash.paid["P1"] = Paid(checkout.order_id, checkout.amount, "BDT", "TRX1")
    assert (await billing.settle(checkout.order_id, "P1", "bkash")).status == "paid"  # type: ignore[union-attr]
    await billing.settle(checkout.order_id, "P1", "bkash")  # the owner reloads: no second month
    async with world.sessions() as s:
        ws = await s.get(Workspace, DEV_WORKSPACE_ID)
    assert ws is not None and ws.plan == "growth"
    assert ws.paid_until == FIXED_NOW + timedelta(days=5)  # an upgrade: the same end day


async def test_a_cancelled_bkash_return_changes_nothing(app: FastAPI) -> None:
    owner = await new_business(app)
    assert (await owner.get("/api/billing")).json()["gateways"] == []  # nothing set up in tests
    back = await owner.get("/billing/nothing/bkash", params={"paymentID": "P1", "status": "cancel"})
    assert back.status_code == 303  # back to the billing page in the same tab
    assert back.headers["location"].endswith("/billing?payment=failed")


def paying(world: World) -> tuple[BillingService, FakePayments]:
    gateway = FakePayments()
    settings = make_test_settings().model_copy(update={"BILLING_STORE_ID": "heydesk"})
    return BillingService(world.sessions, {"sslcommerz": gateway}, settings, world.clock), gateway


async def pay(billing: BillingService, gateway: FakePayments, plan: str, months: int = 1) -> str:
    """Checks out and pays in full; the payment's status after settling."""
    await billing.checkout(DEV_WORKSPACE_ID, plan, months)
    checkout = gateway.started[-1][1]
    gateway.paid[checkout.order_id] = Paid(checkout.order_id, checkout.amount, "BDT", "bank")
    payment = await billing.settle(checkout.order_id, checkout.order_id)
    assert payment is not None
    return payment.status


async def workspace(world: World) -> Workspace:
    async with world.sessions() as s:
        ws = await s.get(Workspace, DEV_WORKSPACE_ID)
    assert ws is not None
    return ws


async def test_an_upgrade_pays_the_difference_and_keeps_the_end_day(world: World) -> None:
    billing, gateway = paying(world)
    await billing.set_plan(DEV_WORKSPACE_ID, "starter", FIXED_NOW + timedelta(days=15))

    q = await billing.quote(DEV_WORKSPACE_ID, "pro", 1)
    per_day = (PLANS["pro"].price - PLANS["starter"].price) / 30
    assert q.kind == "upgrade" and q.amount == round(per_day * 15)  # ৳5,250, not ৳12,990
    assert await pay(billing, gateway, "pro") == "paid"
    ws = await workspace(world)
    assert ws.plan == "pro" and ws.paid_until == FIXED_NOW + timedelta(days=15)  # no free days

    # A year of Starter then a month of Pro no longer gives a year of Pro: it's an upgrade
    await billing.set_plan(DEV_WORKSPACE_ID, "trial", FIXED_NOW)
    assert await pay(billing, gateway, "starter", 12) == "paid"
    q = await billing.quote(DEV_WORKSPACE_ID, "pro", 1)
    assert q.kind == "upgrade" and q.amount == PLANS["pro"].cost(12) - PLANS["starter"].cost(12)


async def test_a_smaller_plan_waits_until_this_one_ends(app: FastAPI, world: World) -> None:
    billing, gateway = paying(world)
    await billing.set_plan(DEV_WORKSPACE_ID, "growth", FIXED_NOW + timedelta(days=10))
    with pytest.raises(Conflict, match="once it ends"):
        await billing.quote(DEV_WORKSPACE_ID, "starter", 1)

    world.clock.advance(days=10)  # Growth has ended (its grace days still answer)
    assert await pay(billing, gateway, "starter") == "paid"
    ws = await workspace(world)
    assert ws.plan == "starter" and ws.paid_until == world.clock.now + timedelta(days=30)


async def test_a_smaller_plan_needs_the_team_to_fit(app: FastAPI) -> None:
    owner = await new_business(app)
    await owner.post("/api/team/members", json={"email": "a@example.com"})
    services = app.state.services
    ws = services.users.principal(owner.headers["Authorization"][7:]).workspace_id
    await services.billing.set_plan(ws, "growth", FIXED_NOW + timedelta(days=1))
    assert (await owner.post("/api/team/members", json={"email": "b@example.com"})).is_success
    await services.billing.set_plan(ws, "growth", FIXED_NOW - timedelta(days=1))
    # Growth has ended; Starter has one moderator, and there are two
    too_many = await owner.get("/api/billing/quote", params={"plan": "starter"})
    assert too_many.status_code == 409 and "Remove 1" in too_many.json()["error"]["message"]


async def test_a_payment_priced_before_the_plan_changed_is_held(world: World) -> None:
    billing, gateway = paying(world)
    await billing.set_plan(DEV_WORKSPACE_ID, "starter", FIXED_NOW + timedelta(days=2))
    await billing.checkout(DEV_WORKSPACE_ID, "pro", 1)  # an upgrade for 2 days: cheap
    upgrade = gateway.started[-1][1]
    assert await pay(billing, gateway, "starter", 12) == "paid"  # then a year of Starter

    gateway.paid["late"] = Paid(upgrade.order_id, upgrade.amount, "BDT", "bank")
    held = await billing.settle(upgrade.order_id, "late")
    assert held is not None and held.status == "review"
    assert (await workspace(world)).plan == "starter"  # not a year of Pro for 2 days' price
    payments = (await billing.billing(DEV_WORKSPACE_ID)).payments
    assert [p.status for p in payments] == ["review", "paid"]
