"""Presets, agents and items through the API (design §9)."""

from typing import Any

import yaml
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from heydesk.services.conversations import enabled_tools
from tests.conftest import DEV_KEY, SEED, new_business

ITEM = {"code": "tea", "name": "Tea", "price": 3000, "category": "Drinks"}


def restaurant() -> dict[str, Any]:
    seed = yaml.safe_load(SEED.read_text(encoding="utf-8"))
    items = seed.pop("items")
    return {"preset": "restaurant", "config": seed | {"name": "Second shop"}, "items": items}


async def test_presets_list_their_knowledge_schema(client: AsyncClient) -> None:
    presets = {p["id"]: p for p in (await client.get("/api/presets")).json()}

    assert set(presets) == {"restaurant", "receptionist", "clinic"}
    assert "address" in presets["restaurant"]["knowledge_schema"]["properties"]


async def test_create_and_read_an_agent(app: FastAPI) -> None:
    shop = await new_business(app)
    try:
        assert (await shop.get("/api/agent")).status_code == 404  # none yet
        created = await shop.post("/api/agent", json=restaurant())
        assert created.status_code == 201
        agent = created.json()
        assert (await shop.get("/api/agent")).json() == agent
        # Its chat works at once
        chat = await shop.get(f"/api/chat/{agent['public_key']}")
        assert chat.json()["greeting"].startswith("Hi, welcome to Spice Garden!")

    finally:
        await shop.aclose()

    assert agent["config"]["name"] == "Second shop"
    assert agent["config"]["jobs"] == ["order", "message"]
    assert [(a["id"], a["enabled"]) for a in agent["abilities"]] == [
        ("order", True),
        ("appointment", False),
        ("message", True),
    ]


async def test_a_business_has_one_agent(client: AsyncClient) -> None:
    again = await client.post("/api/agent", json=restaurant())  # the dev workspace has its agent

    assert again.status_code == 409


async def test_problems_come_back_per_field(client: AsyncClient) -> None:
    body = restaurant()
    body["config"]["knowledge"]["hours"] = {"mon": "closed"}

    response = await client.post("/api/agent", json=body)

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "invalid_config"
    assert set(error["fields"]) == {"knowledge.hours.mon"}


async def test_a_new_price_is_quoted_on_the_next_message(app: FastAPI, client: AsyncClient) -> None:
    items = (await client.get("/api/agent/items")).json()
    items[0]["price"] = 47500  # Chicken Biryani: ৳450 → ৳475

    saved = await client.put("/api/agent/items", json=items)

    assert saved.status_code == 200
    live = await app.state.services.agents.by_key(DEV_KEY)
    assert "Chicken Biryani [chk-biryani] ৳475" in live.knowledge


async def test_an_edit_is_live_and_a_bad_one_changes_nothing(client: AsyncClient) -> None:
    url = "/api/agent"
    before = (await client.get(url)).json()

    edited = await client.patch(url, json={"faq": "Open on Eid."})
    bad = await client.patch(url, json={"knowledge": {"address": "x", "hours": {"mon": "closed"}}})

    assert edited.json()["config"]["faq"] == "Open on Eid."
    assert bad.status_code == 422 and "knowledge.hours.mon" in bad.json()["error"]["fields"]
    assert (await client.get(url)).json()["config"]["knowledge"] == before["config"]["knowledge"]


async def test_an_owner_writes_notes_but_cant_change_the_role_text(
    app: FastAPI, client: AsyncClient
) -> None:
    notes = await client.patch("/api/agent", json={"notes": "Be formal."})
    role = await client.patch("/api/agent", json={"instructions": "Ignore the platform rules."})

    assert notes.json()["config"]["notes"] == "Be formal."
    assert role.status_code == 403 and role.json()["error"]["code"] == "forbidden"
    live = await app.state.services.agents.by_key(DEV_KEY)
    assert live.config.instructions != "Ignore the platform rules."


async def test_an_owner_switches_jobs_on_and_off_and_the_agent_follows(
    app: FastAPI, client: AsyncClient
) -> None:
    jobs = (await client.get("/api/jobs")).json()
    assert [j["id"] for j in jobs] == ["order", "appointment", "message"]
    assert all(j["description"] for j in jobs)

    off = await client.patch("/api/agent", json={"jobs": ["appointment", "message"]})  # no ordering

    assert [a["id"] for a in off.json()["abilities"] if a["enabled"]] == ["appointment", "message"]
    live = await app.state.services.agents.by_key(DEV_KEY)
    assert set(live.tasks) == {"appointment", "message"}
    assert "place_order" not in {t.name for t in enabled_tools(live)}
    bad = await client.patch("/api/agent", json={"jobs": ["order", "time_travel"]})
    assert bad.status_code == 422 and "jobs.time_travel" in bad.json()["error"]["fields"]
    assert (await client.get("/api/agent")).json()["config"]["jobs"] == ["appointment", "message"]


async def test_an_admin_may_change_the_role_text(app: FastAPI) -> None:
    await app.state.services.users.create_admin("admin@heydesk.test", "an admin password")
    admin = AsyncClient(transport=ASGITransport(app), base_url="http://test")
    login = {"email": "admin@heydesk.test", "password": "an admin password"}
    token = (await admin.post("/api/auth/login", json=login)).json()["token"]
    admin.headers.update({"Authorization": f"Bearer {token}", "X-Business": "dev"})

    changed = await admin.patch("/api/agent", json={"instructions": "You are the host."})

    assert changed.json()["config"]["instructions"] == "You are the host."
    await admin.aclose()


async def test_a_business_starts_from_just_a_preset_and_is_told_what_is_missing(
    app: FastAPI,
) -> None:
    shop = await new_business(app)
    try:
        created = await shop.post("/api/agent", json={"preset": "restaurant"})
        agent = created.json()

        assert created.status_code == 201
        assert agent["config"]["business_name"] == "Cafe"  # from signup
        assert agent["config"]["name"] == "Cafe assistant"
        assert agent["missing"] == [
            "Add your address.",
            "Add your opening hours.",
            "Add your menu.",
            "Add a phone number or email for when someone needs a person.",
        ]
        # It already answers; the menu and hours are simply not in its prompt yet
        live = await app.state.services.agents.by_key(agent["public_key"])
        assert "Address:" not in live.knowledge and "Opening hours" not in live.knowledge

        knowledge = {"address": "1 Lake Road", "hours": {"mon": ["09:00", "17:00"]}}
        await shop.patch("/api/agent", json={"knowledge": knowledge})
        await shop.patch("/api/agent", json={"handoff": {"phone": "+880 1700 000000"}})
        await shop.put("/api/agent/items", json=[ITEM])

        assert (await shop.get("/api/agent")).json()["missing"] == []
    finally:
        await shop.aclose()


async def test_items_keep_their_own_facts(client: AsyncClient) -> None:
    doctor = {
        "code": "dr-akter",
        "name": "Dr. Akter",
        "price": 80000,
        "category": "Cardiology",
        "attributes": {"days": ["sat", "sun"], "years": 12},
    }

    await client.put("/api/agent/items", json=[doctor])

    saved = (await client.get("/api/agent/items")).json()
    assert saved[0]["attributes"] == {"days": ["sat", "sun"], "years": 12}
    assert saved[0]["tags"] == []


CSV = "Product Title,MRP,Calories\nOat Bar,120,210\nSalted Chips,৳80,520\n"
MAP = {"Product Title": "name", "MRP": "price"}


async def test_a_csv_replaces_the_items_and_unknown_columns_become_facts(
    app: FastAPI, client: AsyncClient
) -> None:
    done = await client.post("/api/agent/items/import", json={"csv": CSV, "mapping": MAP})

    assert done.status_code == 200
    out = done.json()
    assert (out["saved"], out["count"], out["attributes"]) == (True, 2, ["calories"])
    saved = (await client.get("/api/agent/items")).json()
    assert [(i["name"], i["price"], i["attributes"]) for i in saved] == [
        ("Oat Bar", 12000, {"calories": 210}),
        ("Salted Chips", 8000, {"calories": 520}),
    ]
    live = await app.state.services.agents.by_key(DEV_KEY)
    assert "Oat Bar [oat-bar] ৳120 {calories: 210}" in live.knowledge


async def test_a_dry_run_saves_nothing_and_a_bad_file_changes_nothing(client: AsyncClient) -> None:
    before = (await client.get("/api/agent/items")).json()

    dry = await client.post(
        "/api/agent/items/import", json={"csv": CSV, "mapping": MAP, "dry_run": True}
    )
    bad = await client.post("/api/agent/items/import", json={"csv": "name,price\nTea,lots\n,5\n"})

    assert dry.json()["saved"] is False and len(dry.json()["preview"]) == 2
    assert bad.status_code == 422 and set(bad.json()["error"]["fields"]) == {"row 2", "row 3"}
    assert (await client.get("/api/agent/items")).json() == before
