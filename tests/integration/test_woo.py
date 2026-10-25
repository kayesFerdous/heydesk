"""A shop's list kept in its WooCommerce store, and our orders written there (D75): Woo's
fields become ours, a Read key is refused, an order placed here is created in Woo with our
prices and moved when it moves, and a pull doesn't undo an order not in Woo yet."""

import asyncio
import base64
import hashlib
import hmac
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from heydesk.desks.shop.values import products_from_woo
from heydesk.errors import InvalidConfig
from heydesk.ports.llm import ToolCall
from heydesk.ports.payments import Paid
from heydesk.ports.woo import WooStore, WooUnreachable
from heydesk.services.agents import DEV_WORKSPACE_ID
from heydesk.services.woo import TOPICS, WOO_STATUS
from tests.conftest import SEEDS, World
from tests.integration.test_payments import SHOP_DETAILS, found, paying_first, take
from tests.integration.test_shop import buy, orders, stock

URL = "https://shop.example.com"


def variation(vid: int, size: str, colour: str, stock: int, price: str = "2450") -> dict[str, Any]:
    attributes = [{"name": "Size", "option": size}, {"name": "Colour", "option": colour}]
    return {
        "id": vid,
        "price": price,
        "regular_price": price,
        "manage_stock": True,
        "stock_quantity": stock,
        "stock_status": "instock" if stock > 0 else "outofstock",
        "attributes": attributes,
    }


def tupi(stock: int = 12) -> dict[str, Any]:
    return {
        "id": 10,
        "type": "simple",
        "sku": "TP-1",
        "name": "Cotton Tupi",
        "price": "300",
        "regular_price": "350",
        "short_description": "",
        "description": "<p>White cotton <b>tupi</b>, &amp; soft.</p>",
        "categories": [{"name": "Uncategorized"}],
        "attributes": [],
        "manage_stock": True,
        "stock_quantity": stock,
        "stock_status": "instock",
    }


def panjabi(black_l: int = 3) -> dict[str, Any]:
    return {
        "id": 11,
        "type": "variable",
        "sku": "LP-1",
        "name": "Linen Panjabi",
        "price": "2450",
        "regular_price": "",
        "short_description": "Soft linen for Eid.",
        "categories": [{"name": "Panjabi &amp; Kurta"}],
        "attributes": [
            {"name": "Size", "options": ["M", "L", "XXL"]},
            {"name": "Colour", "options": ["White", "Black"]},
            {"name": "Fabric", "options": ["Linen"]},
        ],
        "manage_stock": False,
        "stock_quantity": None,
        "stock_status": "instock",
        "variations": [
            variation(12, "M", "White", 4),
            variation(13, "L", "White", 0),
            variation(16, "L", "Black", black_l),
            variation(17, "XXL", "Black", 1, price="2650"),
        ],
    }


class FakeWoo:
    """A WooCommerce store: its products, and the orders written into it, with its stock
    taken off and put back as Woo does."""

    def __init__(self) -> None:
        self.items = [tupi(), panjabi()]
        self.orders: dict[int, dict[str, Any]] = {}
        self.writes = True  # a Read/Write key
        self.down = ""  # a problem for every call
        self.url = URL  # the store's address
        self.hooks: dict[int, dict[str, Any]] = {}  # the webhooks made in it

    async def products(self, store: WooStore) -> list[dict[str, Any]]:
        assert (store.url, store.key, store.secret) == (self.url, "ck_1", "cs_1")
        self._check()
        return self.items

    async def can_write(self, store: WooStore) -> bool:
        return self.writes

    async def create_order(self, store: WooStore, body: dict[str, Any]) -> int:
        self._check()
        woo_id = 100 + len(self.orders)
        self.orders[woo_id] = body
        self._take(body, -1)
        return woo_id

    async def set_status(self, store: WooStore, order_id: int, status: str) -> None:
        self._check()
        if status == "cancelled":
            self._take(self.orders[order_id], +1)
        self.orders[order_id]["status"] = status

    async def add_webhook(self, store: WooStore, topic: str, url: str, secret: str) -> int:
        made = max(self.hooks, default=49) + 1
        self.hooks[made] = {"topic": topic, "delivery_url": url, "secret": secret}
        self.hooks[made]["status"] = "active"
        return made

    async def webhook(self, store: WooStore, webhook_id: int) -> dict[str, Any] | None:
        return self.hooks.get(webhook_id)

    async def update_webhook(
        self, store: WooStore, webhook_id: int, changes: dict[str, Any]
    ) -> None:
        self.hooks[webhook_id] |= changes

    async def delete_webhook(self, store: WooStore, webhook_id: int) -> None:
        self.hooks.pop(webhook_id, None)

    def _check(self) -> None:
        if self.down:
            raise WooUnreachable(self.down)

    def _take(self, body: dict[str, Any], sign: int) -> None:
        for line in body["line_items"]:
            for item in self.items:
                for v in [item, *item.get("variations", [])]:
                    if v["id"] == line.get("variation_id", line["product_id"]):
                        v["stock_quantity"] += sign * line["quantity"]


def test_woos_fields_become_ours() -> None:
    external = {"id": 30, "type": "external", "price": "10", "name": "Elsewhere"}
    pictured = tupi() | {"images": [{"src": "https://shop.example.com/tupi.jpg"}]}
    found, ids = products_from_woo([pictured, panjabi(), external])
    assert [p.code for p in found] == ["TP-1", "LP-1", "LP-1-2650"]
    tupi_, linen, xxl = found
    assert (tupi_.photos, linen.photos) == (["https://shop.example.com/tupi.jpg"], [])  # links
    assert (tupi_.price, tupi_.was_price, tupi_.stock) == (30000, 35000, 12)
    assert (tupi_.description, tupi_.category) == ("White cotton tupi, & soft.", "Products")
    assert linen.category == "Panjabi & Kurta"
    assert linen.description == "Soft linen for Eid. Fabric: Linen."
    assert (linen.sizes, linen.colours) == (["M", "L"], ["White", "Black"])
    assert {v.label(): v.stock for v in linen.variants} == {
        "M/White": 4,
        "L/White": 0,
        "L/Black": 3,
        "M/Black": 0,  # Woo has no such variation: never offered (D59)
    }
    assert (xxl.name, xxl.price, xxl.sizes) == ("Linen Panjabi (XXL)", 265000, ["XXL"])
    assert ids["LP-1"] == {"id": 11, "variations": {"M|White": 12, "L|White": 13, "L|Black": 16}}
    assert ids["LP-1-2650"]["variations"] == {"XXL|Black": 17}


async def test_a_read_key_or_a_plain_address_is_refused_but_localhost_in_development(
    world: World,
) -> None:
    await world.agent("shop")
    woo = FakeWoo()
    world.services.woo.api = woo
    with pytest.raises(InvalidConfig, match="https"):
        await world.services.woo.link(DEV_WORKSPACE_ID, "http://shop.example.com", "ck_1", "cs_1")
    world.services.woo.local_http = True  # development: the test shop on this machine
    woo.url = "http://localhost:8088"
    await world.services.woo.link(DEV_WORKSPACE_ID, "http://localhost:8088", "ck_1", "cs_1")
    with pytest.raises(InvalidConfig, match="https"):
        await world.services.woo.link(DEV_WORKSPACE_ID, "http://shop.example.com", "ck_1", "cs_1")
    woo.url, woo.writes = URL, False
    with pytest.raises(InvalidConfig, match="Read/Write"):
        await world.services.woo.link(DEV_WORKSPACE_ID, URL, "ck_1", "cs_1")


async def test_an_order_placed_here_is_written_into_woo_and_its_stock_follows(
    world: World,
) -> None:
    agent = await world.agent("shop")
    woo = FakeWoo()
    world.services.woo.api = woo
    await world.services.woo.link(DEV_WORKSPACE_ID, URL + "/", "ck_1", "cs_1")
    assert await stock(world, agent, "LP-1", "L", "Black") == 3

    woo.down = "Couldn't reach your store. Try again in a minute."
    await buy(world, agent, {"code": "LP-1", "size": "L", "colour": "Black", "quantity": 2})
    await pushed(world)  # the push right after the order couldn't reach it either
    assert woo.orders == {}
    link = await world.services.woo.sync(DEV_WORKSPACE_ID)
    assert link.problem.endswith(
        "wasn't written: Couldn't reach your store. Try again in a minute."
    )
    assert await stock(world, agent, "LP-1", "L", "Black") == 1  # the list stays as it was

    woo.down = ""
    woo.items[1]["variations"][0]["stock_quantity"] = 5  # a pull of a changed store...
    link = await world.services.woo.sync(DEV_WORKSPACE_ID)
    assert link.problem == ""
    [(woo_id, body)] = woo.orders.items()
    assert body["status"] == WOO_STATUS["new"] == "processing"
    assert body["line_items"] == [
        {
            "product_id": 11,
            "variation_id": 16,
            "quantity": 2,
            "subtotal": "4900.00",
            "total": "4900.00",
        }
    ]
    assert body["meta_data"][0]["value"] == "O-101"
    assert await stock(world, agent, "LP-1", "L", "Black") == 1  # Woo's count, ours in it
    assert await stock(world, agent, "LP-1", "M", "White") == 5

    [order] = await orders(world)
    await world.store(agent).move_order(order.id, "cancelled")
    await world.services.woo.sync(DEV_WORKSPACE_ID)
    assert woo.orders[woo_id]["status"] == "cancelled"
    assert await stock(world, agent, "LP-1", "L", "Black") == 3  # back in Woo, and here


async def test_an_order_is_in_woo_as_soon_as_it_is_saved(world: World) -> None:
    agent = await world.agent("shop")
    woo = FakeWoo()
    world.services.woo.api = woo
    await world.services.woo.link(DEV_WORKSPACE_ID, URL, "ck_1", "cs_1")
    await buy(world, agent, {"code": "LP-1", "size": "L", "colour": "Black", "quantity": 1})
    await pushed(world)  # no sync: the turn that saved it wrote it
    [body] = woo.orders.values()
    assert body["meta_data"][0]["value"] == "O-101"
    [order] = await orders(world)
    assert order.woo_id is not None


async def test_an_order_paid_online_is_in_woo_as_soon_as_it_is_paid(world: World) -> None:
    agent, gateway = await paying_first(world.services, "shop")
    woo = FakeWoo()
    world.services.woo.api = woo
    await world.services.woo.link(DEV_WORKSPACE_ID, URL, "ck_1", "cs_1")
    line = {"code": "LP-1", "size": "L", "colour": "Black", "quantity": 1}
    first = [ToolCall("c1", "update_cart", {"lines": [line]})]
    conv = await take(world, agent, first, ToolCall("c2", "place_order", SHOP_DETAILS))
    order = await found(world, conv)
    await pushed(world)
    assert order.status == "unpaid" and woo.orders == {}  # no alert yet, so no push

    gateway.paid["v1"] = Paid(order.id, 6000, "BDT", "B1")
    await world.services.payments.settle(order.id, "v1")
    await pushed(world)  # no sync: the payment wrote it
    [body] = woo.orders.values()
    assert body["status"] == WOO_STATUS["confirmed"]


async def pushed(world: World) -> None:
    """Waits for the pushes the turns started in the background."""
    await asyncio.gather(*world.services.woo.tasks)


async def test_a_pull_doesnt_undo_an_order_not_in_woo_yet(world: World) -> None:
    agent = await world.agent("shop")
    woo = FakeWoo()
    world.services.woo.api = woo
    await world.services.woo.link(DEV_WORKSPACE_ID, URL, "ck_1", "cs_1")
    woo.create_order = refuse  # type: ignore[method-assign]
    await buy(world, agent, {"code": "TP-1", "quantity": 2})
    await pushed(world)
    woo.items[0] = tupi(stock=12)  # the store changed, and our order isn't there yet
    link = await world.services.woo.sync(DEV_WORKSPACE_ID)
    assert "O-101 wasn't written" in link.problem
    assert await stock(world, agent, "TP-1") == 10  # Woo's 12, less our 2


async def refuse(store: WooStore, body: dict[str, Any]) -> int:
    raise WooUnreachable("WooCommerce answered 400.")


async def test_while_a_store_is_linked_the_list_is_changed_there(
    app: FastAPI, client: AsyncClient
) -> None:
    await app.state.services.agents.ensure_dev_agent("shop", SEEDS / "shop.yaml")
    seeded = [p["code"] for p in (await client.get("/api/products")).json()]
    woo = FakeWoo()
    app.state.services.woo.api = woo
    body = {"url": URL, "key": "ck_1", "secret": "cs_1"}
    linked = (await client.put("/api/agent/woo", json=body)).json()
    assert (linked["url"], linked["products"], linked["problem"]) == (URL, 3, "")
    assert "secret" not in (await client.get("/api/agent/woo")).json()
    assert [p["code"] for p in (await client.get("/api/products")).json()] == [
        *seeded,  # Woo's join the list (D120)
        "TP-1",
        "LP-1",
        "LP-1-2650",
    ]
    csv = {"csv": "Name,Price\nTee,350\n"}
    assert (await client.post("/api/products/import", json=csv)).json()["added"] == 1
    sheet = {"url": "https://docs.google.com/spreadsheets/d/abc/edit", "mapping": {}}
    assert (await client.put("/api/agent/sheet", json=sheet)).status_code == 409

    woo.items = [item for item in woo.items if item.get("sku") != "TP-1"]  # deleted in Woo
    assert (await client.post("/api/agent/woo/sync")).json()["problem"] == ""
    codes = [p["code"] for p in (await client.get("/api/products")).json()]
    assert codes == [*seeded, "LP-1", "LP-1-2650", "tee"]  # only Woo's own deleted one went
    assert (await client.delete("/api/agent/woo")).status_code == 204
    assert (await client.get("/api/agent/woo")).status_code == 404
    assert (await client.post("/api/products/import", json=csv)).status_code == 200


HOOKS = "https://heydesk.example.com/api/woo/hook"


async def test_woos_webhooks_are_made_on_linking_and_a_signed_one_syncs(
    app: FastAPI, client: AsyncClient
) -> None:
    await app.state.services.agents.ensure_dev_agent("shop", SEEDS / "shop.yaml")
    woo = FakeWoo()
    service = app.state.services.woo
    service.api, service.hook_base, service.hook_delay = woo, HOOKS, 0
    await client.put("/api/agent/woo", json={"url": URL, "key": "ck_1", "secret": "cs_1"})
    assert sorted(h["topic"] for h in woo.hooks.values()) == sorted(TOPICS)
    [secret] = {h["secret"] for h in woo.hooks.values()}
    assert {h["delivery_url"] for h in woo.hooks.values()} == {f"{HOOKS}/{DEV_WORKSPACE_ID}"}

    woo.items[0] = tupi(stock=4)  # sold on the website
    body = b'{"id": 10}'
    signed = base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()
    path = f"/api/woo/hook/{DEV_WORKSPACE_ID}"
    forged = await client.post(path, content=body, headers={"X-WC-Webhook-Signature": "abc"})
    assert forged.status_code == 404
    assert (await client.post(path, content=b"webhook_id=50")).status_code == 200  # Woo's ping
    sent = await client.post(path, content=body, headers={"X-WC-Webhook-Signature": signed})
    assert sent.status_code == 200
    await asyncio.gather(*service.tasks)
    [tupi_now] = [p for p in (await client.get("/api/products")).json() if p["code"] == "TP-1"]
    assert tupi_now["stock"] == 4

    await client.delete("/api/agent/woo")
    assert woo.hooks == {}  # unlinking deletes them


async def test_the_safety_sync_puts_back_webhooks_woo_turned_off(world: World) -> None:
    await world.agent("shop")
    woo = FakeWoo()
    service = world.services.woo
    service.api, service.hook_base = woo, HOOKS
    await service.link(DEV_WORKSPACE_ID, URL, "ck_1", "cs_1")
    first, second, third = list(woo.hooks)[:3]
    woo.hooks[first]["status"] = "disabled"  # 5 posts failed
    woo.hooks[second]["delivery_url"] = "https://old-tunnel.example.com/api/woo/hook/x"
    del woo.hooks[third]  # deleted in Woo's admin
    await service.keep_hooks(DEV_WORKSPACE_ID)
    assert len(woo.hooks) == len(TOPICS)
    assert {h["status"] for h in woo.hooks.values()} == {"active"}
    assert {h["delivery_url"] for h in woo.hooks.values()} == {f"{HOOKS}/{DEV_WORKSPACE_ID}"}
    assert third not in (await service.get(DEV_WORKSPACE_ID)).hooks.values()


async def test_a_store_linked_without_webhooks_gets_them_at_the_safety_sync(world: World) -> None:
    await world.agent("shop")
    woo = FakeWoo()
    service = world.services.woo
    service.api, service.hook_base = woo, ""  # no public address yet
    await service.link(DEV_WORKSPACE_ID, URL, "ck_1", "cs_1")
    assert woo.hooks == {}
    service.hook_base = HOOKS
    await service.keep_hooks(DEV_WORKSPACE_ID)
    link = await service.get(DEV_WORKSPACE_ID)
    assert sorted(link.hooks) == sorted(TOPICS) and link.hook_secret
    assert {h["secret"] for h in woo.hooks.values()} == {link.hook_secret}
