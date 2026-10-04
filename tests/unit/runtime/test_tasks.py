from zoneinfo import ZoneInfo

import pytest

from heydesk.runtime.schedule import WeeklyHours
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


CLINIC_HOURS = WeeklyHours.model_validate(  # open 9 am to 10 pm every day except Sunday
    {day: ["09:00", "22:00"] for day in ("mon", "tue", "wed", "thu", "fri", "sat")}
)


@pytest.mark.parametrize(
    ("said", "saved"),
    [
        ("tomorrow at 5 pm", "2026-09-30T17:00"),
        ("Tomorrow 5pm", "2026-09-30T17:00"),
        ("today at 8 pm", "2026-09-29T20:00"),
        ("day after tomorrow at 9:30 am", "2026-10-01T09:30"),
        ("saturday 5 pm", "2026-10-03T17:00"),
        ("next Saturday at 17:00", "2026-10-03T17:00"),
        ("tuesday at 6 pm", "2026-09-29T18:00"),  # today is Tuesday and 6 pm is still ahead
        ("tuesday at 10 am", "2026-10-06T10:00"),  # 10 am has passed: next Tuesday
        ("5 Oct 6:30 pm", "2026-10-05T18:30"),
        ("October 6th, 4 pm", "2026-10-06T16:00"),
        ("2026-10-02 3 pm", "2026-10-02T15:00"),
    ],
)
def test_an_appointment_time_is_worked_out_by_python(said: str, saved: str) -> None:
    assert parse_detail("appointment_time", said, NOW, CLINIC_HOURS) == saved


@pytest.mark.parametrize(
    ("said", "hint"),
    [
        ("tomorrow at 11 pm", "Wednesdays we're open 09:00–22:00"),  # the clinic shuts at 10
        ("tomorrow at 8 am", "outside our hours"),
        ("tomorrow at 10 pm", "outside our hours"),  # close is not inclusive
        ("sunday at 5 pm", "closed on Sundays"),
        ("today at 11 am", "has passed"),
        ("tomorrow at 5", "am or pm"),  # never guessed
        ("5 pm", "Which day"),
        ("tomorrow", "Pass a day and a time"),
        ("someday at 5 pm", "Pass the day as"),
        ("31 Feb at 5 pm", "Pass the day as"),
        ("15 Oct at 5 pm", "next 14 days"),
        ("tomorrow at 25:00", "isn't a time of day"),
    ],
)
def test_an_appointment_outside_the_hours_or_unclear_is_refused_with_a_hint(
    said: str, hint: str
) -> None:
    with pytest.raises(ValueError, match=hint):
        parse_detail("appointment_time", said, NOW, CLINIC_HOURS)


def test_with_no_hours_set_only_the_clock_is_checked() -> None:
    assert parse_detail("appointment_time", "tomorrow at 11 pm", NOW) == "2026-09-30T23:00"
    assert parse_detail("time_today", "11 pm", NOW) == "23:00"


def test_a_pickup_time_must_be_inside_the_opening_hours_too() -> None:
    assert parse_detail("time_today", "9 pm", NOW, CLINIC_HOURS) == "21:00"
    with pytest.raises(ValueError, match="outside our hours"):
        parse_detail("time_today", "11 pm", NOW, CLINIC_HOURS)


def test_an_appointment_time_is_shown_in_words() -> None:
    assert display_detail("appointment_time", "2026-09-30T17:00", "BDT") == "Wed 30 Sep, 5:00 pm"
