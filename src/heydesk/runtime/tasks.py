"""Tasks: what a visitor gets done, as a list of details to collect (design §7).

Python decides what to ask next (`next_step`), so the state note and
`place_order` can't disagree. Each detail type has a `parse` (what the visitor
said → the value to save) and a `display`; spoken forms arrive with voice (day 12).
"""

import re
from datetime import datetime, time
from typing import Any, Literal

from pydantic import BaseModel

from heydesk.runtime.speech import clock_time, money

type DetailType = Literal["cart", "text", "person_name", "phone", "time_today"]


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


def parse_detail(kind: DetailType, said: str, now: datetime) -> str:
    """What the visitor said → the value to save. `now` is the business's local time.

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
            return parse_time_today(text, now)


# "7:30 pm", optionally with "today", "tonight" or "at" around it.
TIME = re.compile(
    r"(?:(?:today|tonight)\s+)?(?:at\s+)?"
    r"(\d{1,2})(?:[:.](\d{2}))?\s*(?:([ap])\.?m\.?)?"
    r"(?:\s+(?:today|tonight))?"
)


def parse_time_today(text: str, now: datetime) -> str:
    """Turns "7:30 pm" or "19:30" into "19:30"; a bare "7:30" is the next 7:30 from now.

    It must be later today.
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
    return f"{when:%H:%M}"


def display_detail(kind: DetailType, value: Any, currency: str) -> str:
    """A saved value for the prompt and the read-back."""
    match kind:
        case "cart":
            lines = ", ".join(f"{line['quantity']} × {line['name']}" for line in value["lines"])
            return f"{lines}, total {money(value['total'], currency)}"
        case "time_today":
            return "today, " + clock_time(time.fromisoformat(value))
        case _:
            return str(value)
