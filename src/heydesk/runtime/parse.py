"""What the visitor said → a value Python trusts (rule 1): names, phone numbers, times.

Every parser raises ValueError with a hint for the model ("Ask again.") when the text
doesn't fit, so a tool can hand the hint straight back.
"""

import calendar
import re
from datetime import date, datetime, time, timedelta

from heydesk.runtime.schedule import WeeklyHours
from heydesk.runtime.speech import clock_time


def name(said: str) -> str:
    text = " ".join(said.split())
    if not text or len(text) > 100 or not any(c.isalpha() for c in text):
        raise ValueError("That doesn't look like a name. Ask again.")
    return text


def phone(said: str) -> str:
    number = re.sub(r"[\s\-().]", "", said)
    if not re.fullmatch(r"\+?\d{7,15}", number):
        raise ValueError("That doesn't look like a phone number. Ask for it again.")
    return number


def text(said: str, limit: int = 1000) -> str:
    cleaned = " ".join(said.split())
    if not cleaned:
        raise ValueError("It's empty. Ask again.")
    if len(cleaned) > limit:
        raise ValueError("That's too long. Ask for a shorter version.")
    return cleaned


# "7:30 pm", optionally with "today", "tonight" or "at" around it.
TIME = re.compile(
    r"(?:(?:today|tonight)\s+)?(?:at\s+)?"
    r"(\d{1,2})(?:[:.](\d{2}))?\s*(?:([ap])\.?m\.?)?"
    r"(?:\s+(?:today|tonight))?"
)


def time_today(said: str, now: datetime, hours: WeeklyHours) -> time:
    """ "7:30 pm" or "19:30" → 19:30; a bare "7:30" is the next 7:30 from now.

    `now` is the business's local time. It must be later today, and inside opening hours.
    """
    match = TIME.fullmatch(" ".join(said.lower().split()))
    if match is None:
        raise ValueError(
            "Pass only a clock time for today, like 7:30 pm. Pickup is today only: "
            "if they asked for another day, tell them that and ask for a time today."
        )
    hour, minute, half = int(match[1]), int(match[2] or 0), match[3]
    if minute > 59 or hour > 23 or (half and not 1 <= hour <= 12):
        raise ValueError("That isn't a time of day. Give one like 7:30 pm.")
    if half:
        hour = hour % 12 + (12 if half == "p" else 0)
    elif hour < 12 and time(hour, minute) <= now.time():
        hour += 12  # "7:30" said in the afternoon means this evening
    when = time(hour, minute)
    if when <= now.time():
        raise ValueError(
            f"That time has passed: it's {clock_time(now.time())} now. Ask for a later time today."
        )
    problem = hours.problem_with(datetime.combine(now.date(), when))
    if problem:
        raise ValueError(problem)
    return when


WEEKDAYS = {
    name: n
    for n, names in enumerate(
        [
            ("monday", "mon"),
            ("tuesday", "tue", "tues"),
            ("wednesday", "wed"),
            ("thursday", "thu", "thur", "thurs"),
            ("friday", "fri"),
            ("saturday", "sat"),
            ("sunday", "sun"),
        ]
    )
    for name in names
}
MONTHS = {
    name: n
    for n, names in enumerate(
        [
            ("january", "jan"),
            ("february", "feb"),
            ("march", "mar"),
            ("april", "apr"),
            ("may",),
            ("june", "jun"),
            ("july", "jul"),
            ("august", "aug"),
            ("september", "sep", "sept"),
            ("october", "oct"),
            ("november", "nov"),
            ("december", "dec"),
        ],
        start=1,
    )
    for name in names
}
# The clock time at the end: "11 pm", "7:30 pm", "19:30"
CLOCK_AT_END = re.compile(r"(?:\bat\s+)?\b(\d{1,2})(?::(\d{2}))?\s*(?:([ap])\.?m\.?)?\s*$")
DAYS_AHEAD = 14


def day_and_time(said: str, now: datetime, hours: WeeklyHours) -> datetime:
    """ "tomorrow at 11 pm", "saturday 5pm", "5 Oct 6:30 pm" → that local datetime.

    Python works out the date, so the model never does the arithmetic. It must be in the next
    two weeks, and inside opening hours. A time with no am/pm is asked again, never guessed.
    """
    lowered = said.lower().replace(",", " ")
    clock = CLOCK_AT_END.search(lowered)
    if clock is None:
        raise ValueError("Pass a day and a time, like tomorrow at 5 pm. Ask for the time.")
    hour, minute, half = int(clock[1]), int(clock[2] or 0), clock[3]
    if minute > 59 or hour > 23 or (half and not 1 <= hour <= 12):
        raise ValueError("That isn't a time of day. Ask for one like 5 pm.")
    if half:
        hour = hour % 12 + (12 if half == "p" else 0)
    elif not (hour >= 13 or (clock[2] and hour == 0)):
        raise ValueError(f"Is {lowered[clock.start() :].strip()} am or pm? Ask.")
    day = day_of(lowered[: clock.start()], now, time(hour, minute))
    when = datetime.combine(day, time(hour, minute), tzinfo=now.tzinfo)
    if when <= now:
        raise ValueError("That time has passed. Ask for a later one.")
    if when > now + timedelta(days=DAYS_AHEAD):
        raise ValueError(f"We only take requests for the next {DAYS_AHEAD} days.")
    problem = hours.problem_with(when)
    if problem:
        raise ValueError(problem)
    return when


def day_of(said: str, now: datetime, at: time | None = None) -> date:
    """ "today", "tomorrow", "saturday", "5 oct", "sunday 18 oct" → the date, from `now` (local
    time). A weekday is the next one; today's weekday is today, unless `at` today has already
    passed. A weekday with a date must match it."""
    words = [w for w in said.lower().split() if w not in ("at", "on", "this", "next", "the", "of")]
    today = now.date()
    hint = "Pass the day as today, tomorrow, a weekday like Saturday, or a date like 5 Oct."
    if not words:
        raise ValueError("Which day? Ask for the day as well.")
    if words == ["today"]:
        return today
    if words == ["tomorrow"]:
        return today + timedelta(days=1)
    if words == ["day", "after", "tomorrow"]:
        return today + timedelta(days=2)
    if len(words) == 1 and words[0] in WEEKDAYS:
        ahead = (WEEKDAYS[words[0]] - today.weekday()) % 7
        if ahead == 0 and at is not None and at <= now.time():
            ahead = 7  # that time today has passed: the same day next week
        return today + timedelta(days=ahead)
    if len(words) > 1 and words[0] in WEEKDAYS:  # "sunday 18 oct"
        day = day_of(" ".join(words[1:]), now, at)
        if day.weekday() != WEEKDAYS[words[0]]:
            raise ValueError(
                f"{day.day} {day:%b} is a {day:%A}, not a {calendar.day_name[WEEKDAYS[words[0]]]}. "
                "Ask which day they mean."
            )
        return day
    if len(words) == 1 and re.fullmatch(r"\d{4}-\d{2}-\d{2}", words[0]):
        try:
            return date.fromisoformat(words[0])
        except ValueError:
            raise ValueError(hint) from None
    found = re.fullmatch(
        r"(?:(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]+)|([a-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?)",
        " ".join(words),
    )
    if found:
        number, month = (found[1], found[2]) if found[1] else (found[4], found[3])
        if month in MONTHS:
            try:
                day = date(today.year, MONTHS[month], int(number))
                return day if day >= today else date(today.year + 1, MONTHS[month], int(number))
            except ValueError:
                raise ValueError(hint) from None
    raise ValueError(hint)


def day_time(when: datetime) -> str:
    """How a day and time is shown and read back: "Wed 30 Sep, 5:00 pm"."""
    return f"{when:%a} {when.day} {when:%b}, {clock_time(when.time())}"
