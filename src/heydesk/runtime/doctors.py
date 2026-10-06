"""A clinic's doctors (D37). They are items (D30): `days` and `hours` attributes say when
each one sees patients, and Python, not the model, checks a time against them (rule 1)."""

import re
from datetime import datetime, time

from heydesk.runtime.items import Item
from heydesk.runtime.schedule import DAYS
from heydesk.runtime.speech import clock_time

HOURS = re.compile(r"(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})")  # "17:00-21:00"


def match_doctor(doctors: list[Item], said: str) -> Item:
    """The one doctor `said` names: a code ("dr-akter") or name words ("Dr. Akter").
    Raises ValueError with a hint for the model when it's none or more than one."""
    text = said.lower().strip()
    available = [d for d in doctors if d.available]
    for doctor in available:
        if doctor.code.lower() == text:
            return doctor
    words = [w for w in re.split(r"[\s.]+", text) if w and w not in ("dr", "doctor")]
    found = [d for d in available if words and all(w in d.name.lower() for w in words)]
    if len(found) == 1:
        return found[0]
    if found:
        raise ValueError(f"Which one: {' or '.join(d.name for d in found)}? Ask.")
    raise ValueError(f"{said!r} isn't one of our doctors. Offer one from the list.")


def doctor_problem(doctor: Item, when: datetime) -> str | None:
    """Why `doctor` can't see patients at `when` (local time), in words for the model;
    None if they can. A doctor with no `days` or `hours` set isn't checked on that."""
    days = doctor.attributes.get("days")
    if isinstance(days, list) and DAYS[when.weekday()] not in (d.lower()[:3] for d in days):
        return (
            f"{doctor.name} sees patients on {', '.join(days)}, not on {when:%A}s. "
            "Ask for one of those days."
        )
    hours = HOURS.fullmatch(str(doctor.attributes.get("hours", "")).strip())
    if hours:
        start = time(int(hours[1]), int(hours[2]))
        end = time(int(hours[3]), int(hours[4]))
        if not start <= when.time() < end:
            return (
                f"{doctor.name} sees patients from {clock_time(start)} to {clock_time(end)}. "
                "Ask for a time then."
            )
    return None
