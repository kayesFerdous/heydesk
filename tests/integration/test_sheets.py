"""The products kept in a linked Google Sheet: the link becomes its CSV, the list is the
sheet's, the shop's count is taken only when its cell changed (less the orders not shipped
yet), a sheet with a problem keeps the old list, and while linked the list isn't edited here."""

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from heydesk.errors import InvalidConfig
from heydesk.ports.sheets import SheetUnreadable
from heydesk.services.agents import DEV_WORKSPACE_ID
from heydesk.services.sheets import csv_url
from tests.conftest import SEEDS, World
from tests.integration.test_shop import buy, photo, stock
from tests.integration.test_team import OWNER, link_owner, to

LINK = "https://docs.google.com/spreadsheets/d/abc-123_X/edit#gid=42"
MAPPING = {"code": "Code", "name": "Item", "price": "Price", "stock": "Qty"}


class Sheet:
    """A fake Google Sheet: its text, or the problem reading it."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.problem = ""

    async def __call__(self, url: str) -> str:
        assert url == "https://docs.google.com/spreadsheets/d/abc-123_X/export?format=csv&gid=42"
        if self.problem:
            raise SheetUnreadable(self.problem)
        return self.text


def products(silk: str, kurti_price: str = "950") -> str:
    return (
        f"Code,Item,Price,Qty\npanjabi-silk,Silk Panjabi,3200,{silk}\nkurti,Kurti,{kurti_price},\n"
    )


def test_a_sheets_link_becomes_the_csv_of_its_tab() -> None:
    assert csv_url("https://docs.google.com/spreadsheets/d/abc/edit?usp=sharing").endswith(
        "/d/abc/export?format=csv"
    )
    with pytest.raises(InvalidConfig):
        csv_url("https://example.com/spreadsheets/d/abc")


async def test_the_shelf_count_is_taken_only_when_the_owner_changes_it(world: World) -> None:
    agent = await world.agent()
    sheet = Sheet(products(silk="5"))
    world.services.sheets.fetch = sheet
    await world.services.sheets.link(DEV_WORKSPACE_ID, LINK, MAPPING)
    assert await stock(world, agent, "panjabi-silk") == 5

    code = await buy(world, agent, {"code": "panjabi-silk", "quantity": 1})
    assert await stock(world, agent, "panjabi-silk") == 4

    sheet.text = products(silk="5", kurti_price="990")  # a new price; the count untouched
    await world.services.sheets.sync(DEV_WORKSPACE_ID)
    assert await stock(world, agent, "panjabi-silk") == 4  # our order still counts
    kurti = next(p for p in await world.store(agent).products() if p.code == "kurti")
    assert kurti.price == 99000

    sheet.text = products(silk="6", kurti_price="990")  # restocked: 6 on the shelf
    await world.services.sheets.sync(DEV_WORKSPACE_ID)
    assert await stock(world, agent, "panjabi-silk") == 5  # less the order not shipped yet

    order = await world.store(agent).order(int(code.removeprefix("O-")))
    assert order is not None
    for status in ("confirmed", "shipped"):
        await world.store(agent).move_order(order.id, status)
    sheet.text = products(silk="5", kurti_price="990")  # counted again once it's gone
    await world.services.sheets.sync(DEV_WORKSPACE_ID)
    assert await stock(world, agent, "panjabi-silk") == 5


async def test_a_sheet_with_a_problem_keeps_the_old_list(world: World) -> None:
    agent = await world.agent()
    sheet = Sheet(products(silk="5"))
    world.services.sheets.fetch = sheet
    await world.services.sheets.link(DEV_WORKSPACE_ID, LINK, MAPPING)
    before = await world.store(agent).products()

    sheet.text = products(silk="5", kurti_price="cheap")
    link = await world.services.sheets.sync(DEV_WORKSPACE_ID)
    assert "row 3" in link.problem and "cheap" in link.problem
    assert await world.store(agent).products() == before

    sheet.problem = "Share the sheet as 'Anyone with the link: Viewer', then try again."
    link = await world.services.sheets.sync(DEV_WORKSPACE_ID)
    assert link.problem.startswith("Share the sheet")

    sheet.problem, sheet.text = "", products(silk="7")
    link = await world.services.sheets.sync(DEV_WORKSPACE_ID)
    assert link.problem == "" and await stock(world, agent, "panjabi-silk") == 7


async def test_a_sheet_is_checked_then_linked_and_a_bad_row_alerts_once(
    app: FastAPI, client: AsyncClient
) -> None:
    sheet = Sheet(products(silk="5"))
    app.state.services.sheets.fetch = sheet

    suggested = (await client.post("/api/agent/sheet/suggest", json={"url": LINK})).json()
    assert suggested["headers"] == ["Code", "Item", "Price", "Qty"]
    wrong = await client.put("/api/agent/sheet", json={"url": LINK, "mapping": {"name": "Item"}})
    assert wrong.status_code == 422  # no price: nothing is linked
    assert (await client.get("/api/agent/sheet")).status_code == 404

    body = {"url": LINK, "mapping": MAPPING}
    checked = (await client.put("/api/agent/sheet", json=body | {"dry_run": True})).json()
    assert (checked["saved"], checked["count"]) == (False, 2)
    assert (await client.get("/api/agent/sheet")).status_code == 404  # only checked
    linked = (await client.put("/api/agent/sheet", json=body)).json()
    assert (linked["saved"], linked["count"]) == (True, 2)
    assert (await client.get("/api/agent/sheet")).json()["problem"] == ""

    await link_owner(app, client)
    sheet.text += "kulfi,Kulfi,,\n"  # no price
    for _ in range(2):
        synced = (await client.post("/api/agent/sheet/sync")).json()
    assert "row 4" in synced["problem"]
    alerts = [t for t in to(app, OWNER) if "Google Sheet" in t]
    assert len(alerts) == 1  # once, not on every sync
    assert "kulfi" not in [p["code"] for p in (await client.get("/api/products")).json()]

    assert (await client.delete("/api/agent/sheet")).status_code == 204


async def test_a_linked_shop_still_takes_edits_and_imports_here(
    app: FastAPI, client: AsyncClient
) -> None:
    """The sheet's products join the list; an edit or a file here adds to it, and a row deleted
    in the sheet leaves it, but nothing else does."""
    await app.state.services.agents.ensure_dev_agent(SEEDS / "shop.yaml")
    seeded = [p["code"] for p in (await client.get("/api/products")).json()]
    sheet = Sheet(products(silk="5"))
    app.state.services.sheets.fetch = sheet
    await client.put("/api/agent/sheet", json={"url": LINK, "mapping": MAPPING})
    listed = (await client.get("/api/products")).json()
    assert [p["code"] for p in listed] == seeded  # both were there already: updated in place
    silk = next(p for p in listed if p["code"] == "panjabi-silk")

    file = {"file": ("p.png", photo("red"), "image/png")}
    url = (await client.post("/api/products/photos", files=file)).json()["url"]
    edited = [{**p, "photos": [url]} if p is silk else p for p in listed]
    assert (await client.put("/api/products", json=edited)).status_code == 200
    csv = {"csv": "Name,Price\nTee,350\n"}
    assert (await client.post("/api/products/import", json=csv)).json()["added"] == 1

    sheet.text = "Code,Item,Price,Qty\npanjabi-silk,Silk Panjabi,3300,5\n"  # kurti deleted there
    await client.post("/api/agent/sheet/sync")
    now = {p["code"]: p for p in (await client.get("/api/products")).json()}
    assert "kurti" not in now and "tee" in now  # the file's Tee stays
    assert (now["panjabi-silk"]["price"], now["panjabi-silk"]["photos"]) == (330000, [url])
