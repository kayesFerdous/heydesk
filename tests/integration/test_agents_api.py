"""Presets, agents and items through the API (design §9)."""

from typing import Any

import yaml
from fastapi import FastAPI
from httpx import AsyncClient

from tests.conftest import DEV_KEY, SEED, new_business


def restaurant() -> dict[str, Any]:
    seed = yaml.safe_load(SEED.read_text(encoding="utf-8"))
    items = seed.pop("items")
    return {"preset": "restaurant", "config": seed | {"name": "Second shop"}, "items": items}


async def test_presets_list_their_knowledge_schema(client: AsyncClient) -> None:
    presets = {p["id"]: p for p in (await client.get("/api/presets")).json()}

    assert set(presets) == {"restaurant", "receptionist"}
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
    assert agent["config"]["tools"] == [
        "update_order",
        "search_items",
        "set_details",
        "place_order",
        "submit_request",
        "handoff_to_human",
    ]


async def test_a_business_has_one_agent(client: AsyncClient) -> None:
    again = await client.post("/api/agent", json=restaurant())  # the dev workspace has its agent

    assert again.status_code == 409


async def test_problems_come_back_per_field(client: AsyncClient) -> None:
    body = restaurant()
    del body["config"]["knowledge"]["address"]
    body["config"]["tools"] = ["update_order", "fly_to_moon"]

    response = await client.post("/api/agent", json=body)

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "invalid_config"
    assert set(error["fields"]) == {"knowledge.address", "tools.fly_to_moon"}


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
    bad = await client.patch(url, json={"tools": ["fly_to_moon"]})

    assert edited.json()["config"]["faq"] == "Open on Eid."
    assert bad.status_code == 422 and "tools.fly_to_moon" in bad.json()["error"]["fields"]
    assert (await client.get(url)).json()["config"]["tools"] == before["config"]["tools"]
