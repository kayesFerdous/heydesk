"""Writes that land at the same moment, on Postgres as in production. SQLite writes one
transaction at a time, so these can't go wrong there and are skipped.

Run them with `POSTGRES_TEST_URL=postgresql://…`, an empty database: its tables are dropped
and made again. CI's postgres job does."""

import asyncio
import os
from collections.abc import AsyncIterator
from dataclasses import replace
from typing import Any

import pytest
from sqlalchemy import select, text, update

from heydesk.adapters.llm.fake import FakeLLM
from heydesk.db import Base, make_engine
from heydesk.models import Conversation, TeamMessage, Visitor
from heydesk.services.agents import LiveAgent
from heydesk.shop.models import CouponRow, ProductRow, ShopOrder
from heydesk.shop.orders import NewOrder
from heydesk.wiring import build_services
from tests.conftest import SEEDS, Clock, World, make_knowledge, make_test_settings

URL = os.environ.get("POSTGRES_TEST_URL", "")

pytestmark = pytest.mark.skipif(not URL, reason="needs POSTGRES_TEST_URL")

AT_ONCE = 5


@pytest.fixture
async def pg() -> AsyncIterator[World]:
    """`world` on Postgres."""
    # the async driver, as `Settings` names it for the app
    engine, sessions = make_engine(URL.replace("postgresql://", "postgresql+asyncpg://", 1))
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    async def connect() -> None:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            await asyncio.sleep(0.05)  # all open together

    # Connections open first: else the first save is done before the next one connects
    await asyncio.gather(*(connect() for _ in range(AT_ONCE)))
    llm, clock = FakeLLM(), Clock()
    embedder, store, photos = make_knowledge()
    services = build_services(sessions, llm, embedder, store, photos, make_test_settings(), clock)
    yield World(services, sessions, llm, clock, embedder)
    await store.client.close()
    await engine.dispose()


async def shop(pg: World, stock: int | None) -> tuple[LiveAgent, NewOrder, str]:
    """The dev shop with one product left at `stock`, and an order for one of it."""
    agent = await pg.services.agents.ensure_dev_agent(SEEDS / "shop.yaml")
    async with pg.sessions() as s, s.begin():
        row = await s.scalar(
            select(ProductRow).where(ProductRow.workspace_id == agent.workspace_id).limit(1)
        )
        assert row is not None
        await s.execute(
            update(ProductRow).where(ProductRow.id == row.id).values(stock=stock, variants=[])
        )
    line = {"code": row.code, "name": row.name, "size": "", "colour": "", "quantity": 1}
    order = NewOrder(
        lines=[{**line, "price": row.price}],
        subtotal=row.price,
        delivery_charge=60,
        currency="BDT",
        zone="Dhaka",
        address="House 12, Road 5, Mirpur 10",
        name="Kayes",
        phone="01712345678",
    )
    return agent, order, row.id


async def at_once(*saves: Any) -> list[str]:
    """What each save came to: the row's class, or the error's."""
    done = await asyncio.gather(*saves, return_exceptions=True)
    return sorted(type(d).__name__ for d in done)


async def test_the_last_one_goes_to_one_buyer(pg: World) -> None:
    agent, order, product_id = await shop(pg, stock=1)
    store = pg.store(agent)

    said = await at_once(*(store.add_team_order(order) for _ in range(AT_ONCE)))

    assert said == ["OutOfStock"] * (AT_ONCE - 1) + ["ShopOrder"]
    async with pg.sessions() as s:
        assert await s.scalar(select(ProductRow.stock).where(ProductRow.id == product_id)) == 0


async def test_records_saved_at_once_each_get_their_own_number(pg: World) -> None:
    agent = await pg.services.agents.ensure_dev_agent(SEEDS / "shop.yaml")
    store = pg.store(agent)

    said = await at_once(
        *(store.add(TeamMessage, name="Kayes", phone="017", body="Call me") for _ in range(AT_ONCE))
    )

    assert said == ["TeamMessage"] * AT_ONCE
    async with pg.sessions() as s:
        numbers = sorted(await s.scalars(select(TeamMessage.number)))
    assert numbers == list(range(101, 101 + AT_ONCE))


async def test_a_coupon_for_one_use_is_used_once(pg: World) -> None:
    agent, order, _ = await shop(pg, stock=None)  # not counted: only the coupon is in the way
    async with pg.sessions() as s, s.begin():
        s.add(
            CouponRow(
                workspace_id=agent.workspace_id, code="ONCE", kind="amount", value=10, max_uses=1
            )
        )
    with_coupon = replace(order, coupon="ONCE", discount=10)
    store = pg.store(agent)

    said = await at_once(*(store.add_team_order(with_coupon) for _ in range(AT_ONCE)))

    assert said == ["CouponGone"] * (AT_ONCE - 1) + ["ShopOrder"]
    async with pg.sessions() as s:
        assert await s.scalar(select(CouponRow.used)) == 1


async def test_an_order_cancelled_twice_at_once_goes_back_in_stock_once(pg: World) -> None:
    agent, order, product_id = await shop(pg, stock=3)
    store = pg.store(agent)
    placed = await store.add_team_order(order)  # 2 left

    await at_once(*(store.move_order(placed.id, "cancelled") for _ in range(AT_ONCE)))

    async with pg.sessions() as s:
        assert await s.scalar(select(ProductRow.stock).where(ProductRow.id == product_id)) == 3
        assert await s.scalar(select(ShopOrder.status)) == "cancelled"


async def test_lines_added_to_a_chat_at_once_are_all_kept(pg: World) -> None:
    agent = await pg.services.agents.ensure_dev_agent(SEEDS / "shop.yaml")
    conversation = pg.services.conversations._new_conversation(agent, "web", "a-browser")
    async with pg.sessions() as s, s.begin():
        s.add(conversation)

    await at_once(
        *(
            pg.services.inbox.team_reply(agent.workspace_id, conversation.id, f"Reply {n}")
            for n in range(AT_ONCE)
        )
    )

    async with pg.sessions() as s:
        saved = await s.get_one(Conversation, conversation.id)
    assert sorted(m["content"] for m in saved.messages) == [f"Reply {n}" for n in range(AT_ONCE)]


async def test_a_new_visitors_first_two_messages_at_once_both_count(pg: World) -> None:
    agent = await pg.services.agents.ensure_dev_agent(SEEDS / "shop.yaml")

    said = await at_once(
        pg.services.visitors.count_turn(agent, "messenger:new"),
        pg.services.visitors.count_turn(agent, "messenger:new"),
    )

    assert said == ["NoneType", "NoneType"]  # neither failed
    async with pg.sessions() as s:
        assert await s.scalar(select(Visitor.turns).where(Visitor.key == "messenger:new")) == 2
