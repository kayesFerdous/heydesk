from zoneinfo import ZoneInfo

import pytest

from heydesk.runtime.tasks import (
    DetailType,
    current_task,
    display_detail,
    filled_details,
    next_step,
    parse_detail,
)
from tests.conftest import CART, FIXED_NOW, TASKS

NOW = FIXED_NOW.astimezone(ZoneInfo("Asia/Dhaka"))  # Tuesday 14:00
ORDER = TASKS["order"]


@pytest.mark.parametrize(
    ("said", "saved"),
    [
        ("7:30 pm", "19:30"),
        ("7.30PM", "19:30"),
        ("8 p.m.", "20:00"),
        ("19:30", "19:30"),
        ("7:30", "19:30"),  # said at 2 pm, so this evening
        ("3", "15:00"),
        ("at 4pm", "16:00"),
        ("today at 4 pm", "16:00"),
        ("8 tonight", "20:00"),
    ],
)
def test_a_time_today(said: str, saved: str) -> None:
    assert parse_detail("time_today", said, NOW) == saved


@pytest.mark.parametrize("said", ["11 am", "13:59", "2 pm", "soon", "25:00", "7:75", "13 pm"])
def test_a_time_that_has_passed_or_isnt_a_time_is_refused(said: str) -> None:
    with pytest.raises(ValueError):
        parse_detail("time_today", said, NOW)


def test_a_passed_time_says_what_time_it_is() -> None:
    with pytest.raises(ValueError, match="it's 2:00 pm now"):
        parse_detail("time_today", "11 am", NOW)


def test_another_day_is_refused_with_a_reason() -> None:
    with pytest.raises(ValueError, match="today only"):
        parse_detail("time_today", "tomorrow at 4pm", NOW)


def test_names_phones_and_text_are_tidied() -> None:
    assert parse_detail("person_name", "  Kayes   Fardows ", NOW) == "Kayes Fardows"
    assert parse_detail("phone", "017-1234 5678", NOW) == "01712345678"
    assert parse_detail("phone", "+880 1712 345678", NOW) == "+8801712345678"
    assert parse_detail("text", " no onions, please ", NOW) == "no onions, please"


@pytest.mark.parametrize(
    ("kind", "said"),
    [
        ("person_name", "12345"),
        ("phone", "call me"),
        ("phone", "12345"),
        ("text", "   "),
        ("cart", "two biryanis"),  # update_order sets the cart
    ],
)
def test_details_that_dont_fit_are_refused(kind: DetailType, said: str) -> None:
    with pytest.raises(ValueError):
        parse_detail(kind, said, NOW)


def test_display() -> None:
    assert display_detail("cart", CART, "BDT") == "2 × Chicken Biryani, 1 × Borhani, total ৳1,030"
    assert display_detail("time_today", "19:30", "BDT") == "today, 7:30 pm"
    assert display_detail("phone", "01712345678", "BDT") == "01712345678"


def test_next_step_is_the_first_required_detail_still_missing() -> None:
    have = filled_details(ORDER, {"cart": CART, "details": {"name": "Kayes"}})
    assert set(have) == {"items", "name"}
    step = next_step(ORDER, have)
    assert step is not None and step.name == "pickup_time"

    have |= {"pickup_time": "19:30", "phone": "01712345678"}
    assert next_step(ORDER, have) is None  # the note is optional: time to read back


def test_an_empty_cart_is_still_missing() -> None:
    step = next_step(ORDER, filled_details(ORDER, {"cart": {"lines": [], "total": 0}}))
    assert step is not None and step.name == "items"


def test_the_task_is_set_by_set_details_or_started_by_a_cart() -> None:
    assert current_task(TASKS, {}) is None
    assert current_task(TASKS, {"cart": {"lines": [], "total": 0}}) is None
    assert current_task(TASKS, {"cart": CART}) == "order"
    assert current_task(TASKS, {"task": "message"}) == "message"
