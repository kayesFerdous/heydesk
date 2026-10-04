"""Tasks: what a visitor gets done, as a list of details to collect (design §7).

Python decides what to ask next (`next_step`), so the state note and
`place_order` can't disagree. Each detail type has a `parse` (what the visitor
said → the value to save) and a `display`; `speakable()` says it on voice (D20).
"""

import re
from datetime import date, datetime, time, timedelta
from typing import Any, Literal

from pydantic import BaseModel

from heydesk.runtime.schedule import WeeklyHours
from heydesk.runtime.speech import clock_time, money

type DetailType = Literal["cart", "text", "person_name", "phone", "time_today", "appointment_time"]


class Detail(BaseModel):
    name: str  # "pickup_time"
    type: DetailType
    ask: str = ""  # the suggested question
    required: bool = True

    @property
    def label(self) -> str:
        return self.name.replace("_", " ")


class Task(BaseModel):
    label: str  # "pickup order"
    start_when: str  # "the visitor wants to order food"
    details: list[Detail]  # asked in this order
    finish_with: str  # the tool that saves it: "place_order"

    @property
    def kind(self) -> str:
        """What it leaves for the business: an `order` (place_order) or a `request`."""
        return "order" if self.finish_with == "place_order" else "request"


def current_task(tasks: dict[str, Task], state: dict[str, Any]) -> str | None:
    """The task `set_details` started; or, once there's a cart, the task that collects one."""
    if state.get("task") in tasks:
        return str(state["task"])
    if state.get("cart", {}).get("lines"):
        for task_id, task in tasks.items():
            if any(d.type == "cart" for d in task.details):
                return task_id
    return None


def filled_details(task: Task, state: dict[str, Any]) -> dict[str, Any]:
    """The details with a value: the cart from `update_order`, the rest from `set_details`."""
    saved = state.get("details", {})
    have = {}
    for d in task.details:
        if d.type == "cart":
            if state.get("cart", {}).get("lines"):
                have[d.name] = state["cart"]
        elif d.name in saved:
            have[d.name] = saved[d.name]
    return have


def describe(task: Task, filled: dict[str, Any], currency: str) -> list[str]:
    """What's known, one "label = value" each: for the state note and the read-back check."""
    return [
        f"{d.label} = {display_detail(d.type, filled[d.name], currency)}"
        for d in task.details
        if d.name in filled
    ]


def next_step(task: Task, filled: dict[str, Any]) -> Detail | None:
    """The first required detail still missing, or None: everything's there, so read it back."""
    return next((d for d in task.details if d.required and d.name not in filled), None)


def parse_detail(
    kind: DetailType, said: str, now: datetime, hours: WeeklyHours | None = None
) -> str:
    """What the visitor said → the value to save. `now` is the business's local time, and a
    time must fall inside the business's `hours` (when it has set them).

    Raises ValueError with a hint for the model when it doesn't fit.
    """
    text = " ".join(said.split())
    if not text:
        raise ValueError("It's empty. Ask again.")
    match kind:
        case "cart":
            raise ValueError("The items are set with update_order, not here.")
        case "text":
            if len(text) > 1000:
                raise ValueError("That's too long. Ask for a shorter version.")
            return text
        case "person_name":
            if len(text) > 100 or not any(c.isalpha() for c in text):
                raise ValueError("That doesn't look like a name. Ask again.")
            return text
        case "phone":
            number = re.sub(r"[\s\-().]", "", text)
            if not re.fullmatch(r"\+?\d{7,15}", number):
                raise ValueError("That doesn't look like a phone number. Ask for it again.")
            return number
        case "time_today":
            return parse_time_today(text, now, hours)
        case "appointment_time":
            return parse_appointment_time(text, now, hours)


# "7:30 pm", optionally with "today", "tonight" or "at" around it.
TIME = re.compile(
    r"(?:(?:today|tonight)\s+)?(?:at\s+)?"
    r"(\d{1,2})(?:[:.](\d{2}))?\s*(?:([ap])\.?m\.?)?"
    r"(?:\s+(?:today|tonight))?"
)


def parse_time_today(text: str, now: datetime, hours: WeeklyHours | None = None) -> str:
    """Turns "7:30 pm" or "19:30" into "19:30"; a bare "7:30" is the next 7:30 from now.

    It must be later today, and inside opening hours.
    """
    match = TIME.fullmatch(text.lower())
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
    problem = hours.problem_with(datetime.combine(now.date(), when)) if hours else None
    if problem:
        raise ValueError(problem)
    return f"{when:%H:%M}"


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
APPOINTMENT_DAYS = 14


def parse_appointment_time(text: str, now: datetime, hours: WeeklyHours | None = None) -> str:
    """ "tomorrow at 11 pm", "saturday 5pm", "5 Oct 6:30 pm" → "2026-10-05T23:00" (local time).

    Python works out the date, so the model never does the arithmetic. It must be in the next
    two weeks, and inside opening hours. A time with no am/pm is asked again, never guessed.
    """
    lowered = text.lower().replace(",", " ")
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
    day = _day_of(lowered[: clock.start()].split(), now, time(hour, minute))
    when = datetime.combine(day, time(hour, minute), tzinfo=now.tzinfo)
    if when <= now:
        raise ValueError("That time has passed. Ask for a later one.")
    if when > now + timedelta(days=APPOINTMENT_DAYS):
        raise ValueError(f"We only take requests for the next {APPOINTMENT_DAYS} days.")
    problem = hours.problem_with(when) if hours else None
    if problem:
        raise ValueError(problem)
    return f"{when:%Y-%m-%dT%H:%M}"


def _day_of(words: list[str], now: datetime, at: time) -> date:
    words = [w for w in words if w not in ("at", "on", "this", "next", "the", "of")]
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
        if ahead == 0 and at <= now.time():
            ahead = 7  # that time today has passed: the same day next week
        return today + timedelta(days=ahead)
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


def display_detail(kind: DetailType, value: Any, currency: str) -> str:
    """A saved value for the prompt and the read-back."""
    match kind:
        case "cart":
            lines = ", ".join(f"{line['quantity']} × {line['name']}" for line in value["lines"])
            return f"{lines}, total {money(value['total'], currency)}"
        case "time_today":
            return "today, " + clock_time(time.fromisoformat(value))
        case "appointment_time":
            when = datetime.fromisoformat(value)
            return f"{when:%a} {when.day} {when:%b}, {clock_time(when.time())}"
        case _:
            return str(value)
