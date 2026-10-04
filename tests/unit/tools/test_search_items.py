"""`search_items`: the model says what the visitor means; code decides what matches (D28)."""

import json

from heydesk.ports.llm import ToolCall
from heydesk.runtime.items import Item, ItemQuery, find_items
from heydesk.runtime.tool import run_tool
from heydesk.tools.items import TOOL
from tests.conftest import ListCatalog, make_ctx


def snack(code: str, name: str, price: int, **facts: object) -> Item:
    tags = facts.pop("tags", [])
    return Item.model_validate(
        {
            "code": code,
            "name": name,
            "price": price,
            "category": "Snacks",
            "tags": tags,
            "attributes": facts,
        }
    )


SHOP = [
    snack("chips", "Salted Chips", 8000, calories=520, sugar_g=1, diet="vegan"),
    snack("bar", "Oat Bar", 12000, calories=210, sugar_g=9, tags=["healthy"], diet="vegetarian"),
    snack("nuts", "Roasted Almonds", 25000, calories=180, sugar_g=2, tags=["healthy", "popular"]),
    snack("salad", "Grilled Chicken Salad", 28000, calories=320, sugar_g=3, diet="gluten-free"),
    snack("cake", "Chocolate Cake", 22000, calories=450, sugar_g=30),
    Item(
        code="old",
        name="Old Healthy Mix",
        price=100,
        category="Snacks",
        tags=["healthy"],
        available=False,
    ),
]


def codes(**query: object) -> list[str]:
    return [i.code for i in find_items(SHOP, ItemQuery.model_validate(query))]


def test_words_match_name_description_tags_and_facts_and_rank_by_how_many() -> None:
    assert codes(query="salad") == ["salad"]
    assert codes(query="vegan") == ["chips"]  # a fact's value
    assert codes(query="healthy almonds") == ["nuts", "bar"]  # two words beat one
    assert codes(query="pizza") == []


def test_unavailable_items_are_never_offered() -> None:
    assert "old" not in codes(query="healthy")


def test_healthy_means_what_the_business_tagged_or_the_numbers_say() -> None:
    assert codes(tags=["healthy"]) == ["bar", "nuts"]
    light = [{"field": "calories", "op": "at_most", "value": 350}]
    low_sugar = [{"field": "sugar_g", "op": "at_most", "value": 5}]
    assert codes(filters=light + low_sugar) == ["nuts", "salad"]  # untagged salad found by numbers


def test_price_limits_are_in_whole_currency_units() -> None:
    assert codes(max_price=120) == ["chips", "bar"]  # ৳120 = 12000 minor units
    assert codes(min_price=250, max_price=280) == ["nuts", "salad"]


def test_a_missing_or_wrong_kind_of_fact_never_matches() -> None:
    assert codes(filters=[{"field": "calories", "op": "at_most", "value": "lots"}]) == []
    assert codes(filters=[{"field": "colour", "op": "equals", "value": "red"}]) == []
    assert codes(filters=[{"field": "diet", "op": "at_least", "value": 1}]) == []


def test_a_list_fact_is_checked_with_includes() -> None:
    doctors = [
        Item.model_validate(
            {
                "code": "a",
                "name": "Dr A",
                "price": 1,
                "category": "Heart",
                "attributes": {"days": ["sat", "sun"]},
            }
        ),
        Item.model_validate(
            {
                "code": "b",
                "name": "Dr B",
                "price": 1,
                "category": "Skin",
                "attributes": {"days": ["mon"]},
            }
        ),
    ]
    sat = ItemQuery.model_validate(
        {"filters": [{"field": "days", "op": "includes", "value": "Sat"}]}
    )

    assert [d.code for d in find_items(doctors, sat)] == ["a"]
    assert [d.code for d in find_items(doctors, ItemQuery(category="skin"))] == ["b"]


async def test_the_tool_returns_the_best_ten_and_says_when_there_are_more() -> None:
    many = [snack(f"s{n}", f"Snack {n}", 1000 + n) for n in range(25)]
    ctx = make_ctx(items=ListCatalog(many))

    result = await run_tool(TOOL, ToolCall("c1", "search_items", {"query": "snack"}), ctx)

    assert result.ok and result.data["total"] == 25 and len(result.data["items"]) == 10
    assert "Add a condition" in result.data["hint"]


async def test_the_tool_tells_the_model_when_nothing_matched() -> None:
    ctx = make_ctx(items=ListCatalog(SHOP))

    result = await run_tool(TOOL, ToolCall("c1", "search_items", {"query": "pizza"}), ctx)

    assert result.ok and result.data["items"] == [] and "Nothing matched" in result.data["hint"]


def test_the_tool_schema_has_no_refs_for_gpt_oss() -> None:
    schema = json.dumps(TOOL.spec.input_schema)

    assert "$ref" not in schema and "at_most" in schema
