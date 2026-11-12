from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from heydesk.runtime import parse
from heydesk.runtime.schedule import WeeklyHours
from heydesk.runtime.speech import day_time
from tests.conftest import FIXED_NOW

DHAKA = ZoneInfo("Asia/Dhaka")
NOW = FIXED_NOW.astimezone(DHAKA)  # Tuesday 14:00
NO_HOURS = WeeklyHours()
SHOP_HOURS = WeeklyHours.model_validate(  # open 9 am to 10 pm every day except Sunday
    {day: ["09:00", "22:00"] for day in ("mon", "tue", "wed", "thu", "fri", "sat")}
)


def day_and_time(said: str, hours: WeeklyHours = SHOP_HOURS) -> str:
    return f"{parse.day_and_time(said, NOW, hours):%Y-%m-%dT%H:%M}"


def test_names_phones_and_text_are_tidied() -> None:
    assert parse.name("  Kayes   Fardows ") == "Kayes Fardows"
    assert parse.phone("017-1234 5678") == "01712345678"
    assert parse.phone("+880 1712 345678") == "+8801712345678"
    assert parse.text(" no onions, please ") == "no onions, please"


@pytest.mark.parametrize(
    ("parser", "said"),
    [(parse.name, "12345"), (parse.phone, "call me"), (parse.phone, "12345"), (parse.text, "  ")],
)
def test_details_that_dont_fit_are_refused(parser: object, said: str) -> None:
    with pytest.raises(ValueError):
        parser(said)  # type: ignore[operator]


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
        (
            "Saturday 3 Oct at 5 pm",
            "2026-10-03T17:00",
        ),  # the day, date and time written out in full
        ("Sat 3 Oct 5 pm", "2026-10-03T17:00"),
    ],
)
def test_a_day_and_time_is_worked_out_by_python(said: str, saved: str) -> None:
    assert day_and_time(said) == saved


@pytest.mark.parametrize(
    ("said", "hint"),
    [
        ("tomorrow at 11 pm", "Wednesdays we're open 09:00–22:00"),  # the shop shuts at 10
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
        ("Friday 3 Oct at 5 pm", "3 Oct is a Saturday, not a Friday"),  # never guessed
    ],
)
def test_a_day_and_time_outside_the_hours_or_unclear_is_refused_with_a_hint(
    said: str, hint: str
) -> None:
    with pytest.raises(ValueError, match=hint):
        day_and_time(said)


def test_with_no_hours_set_only_the_clock_is_checked() -> None:
    assert day_and_time("tomorrow at 11 pm", NO_HOURS) == "2026-09-30T23:00"


def test_closed_every_day_refuses_every_time() -> None:
    # The editor saves all seven days; every one off is closed, not "no hours yet"
    all_off = WeeklyHours.model_validate(
        dict.fromkeys(("mon", "tue", "wed", "thu", "fri", "sat", "sun"))
    )
    with pytest.raises(ValueError, match="closed on Tuesdays"):
        day_and_time("today at 4:05 pm", all_off)


def test_a_bare_weekday_is_the_next_one_or_today() -> None:
    assert parse.day_of("tuesday", NOW).isoformat() == "2026-09-29"  # today
    assert parse.day_of("Sat", NOW).isoformat() == "2026-10-03"


def test_a_day_and_time_is_shown_in_words() -> None:
    assert day_time(datetime(2026, 9, 30, 17, 0, tzinfo=DHAKA)) == "Wed 30 Sep, 5:00 pm"
