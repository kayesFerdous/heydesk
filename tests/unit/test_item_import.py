"""Items from a business's own spreadsheet (D29)."""

import pytest

from heydesk.errors import InvalidConfig
from heydesk.services.item_import import parse_items

SHOP = """Product Title,MRP,Brand,Calories,Diet,Tags,In Stock
Salted Chips,৳80,Pran,520,vegan,,yes
Oat Bar,"1,200.50",Kellogg's,210,vegetarian; gluten-free,"healthy, snack",no
Roasted Almonds,250,,180,,healthy,
"""
MAPPING = {"Product Title": "name", "MRP": "price", "In Stock": "available"}


def test_a_shops_columns_become_fields_and_attributes() -> None:
    items, attributes = parse_items(SHOP, MAPPING)

    assert attributes == ["brand", "calories", "diet"]
    chips, bar, nuts = items
    assert (chips.code, chips.name, chips.price, chips.category) == (
        "salted-chips",
        "Salted Chips",
        8000,
        "General",
    )
    assert chips.attributes == {"brand": "Pran", "calories": 520, "diet": "vegan"}
    assert bar.price == 120050 and bar.available is False
    assert bar.tags == ["healthy", "snack"]
    assert bar.attributes["diet"] == ["vegetarian", "gluten-free"]  # a list, split on ;
    assert nuts.available is True and "brand" not in nuts.attributes  # an empty cell is no fact


def test_a_clinics_sheet_with_the_ours_headers_needs_no_mapping() -> None:
    doctors = (
        "code,name,category,price,days,languages,years\n"
        "dr-akter,Dr. Nasrin Akter,Cardiology,800,sat; sun; mon,english; bangla,12\n"
    )

    [akter], attributes = parse_items(doctors)

    assert (akter.code, akter.category, akter.price) == ("dr-akter", "Cardiology", 80000)
    assert akter.attributes == {
        "days": ["sat", "sun", "mon"],
        "languages": ["english", "bangla"],
        "years": 12,
    }
    assert attributes == ["days", "languages", "years"]


def test_made_up_codes_never_clash_and_a_repeated_given_code_is_a_problem() -> None:
    items, _ = parse_items("name,price\nTea,30\nTea,35\n")
    assert [i.code for i in items] == ["tea", "tea-2"]

    with pytest.raises(InvalidConfig) as caught:
        parse_items("code,name,price\nt1,Tea,30\nt1,Coffee,40\n")
    assert "used twice" in caught.value.fields["row 3"]


def test_every_bad_row_is_reported_at_once_and_a_bad_file_is_refused() -> None:
    with pytest.raises(InvalidConfig) as caught:
        parse_items("name,price,available\nTea,thirty,\n,40,\nCoffee,40,maybe\nBun,10,yes\n")
    assert set(caught.value.fields) == {"row 2", "row 3", "row 4"}
    assert "isn't a price" in caught.value.fields["row 2"]
    assert "name is empty" in caught.value.fields["row 3"]
    assert "yes or no" in caught.value.fields["row 4"]

    with pytest.raises(InvalidConfig) as no_price:
        parse_items("Title,Cost\nTea,30\n")
    assert "csv" in no_price.value.fields


def test_blank_lines_and_a_byte_order_mark_are_ignored() -> None:
    items, _ = parse_items("﻿name,price\nTea,30\n\n,\n")
    assert [i.name for i in items] == ["Tea"]
