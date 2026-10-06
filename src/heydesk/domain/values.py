"""Values that carry a guarantee in their type (rule 2)."""

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class Confirmed[T]:
    """`value`, read back to the visitor, who said yes.

    Only `runtime.guards.confirm` makes one (`scripts/check_imports.sh` checks),
    so a service that takes `Confirmed[...]` can't be called without the commit check.
    """

    value: T


@dataclass(frozen=True)
class NewOrder:
    """An order ready to save. Money in minor units; `pickup_time` in UTC."""

    lines: list[dict[str, Any]]  # {code, name, quantity, price}, as update_order saved them
    total: int
    currency: str
    pickup_time: datetime
    customer_name: str
    customer_phone: str
    note: str = ""
    task: str = "order"  # the job it belongs to


@dataclass(frozen=True)
class NewRequest:
    """A message for the team, ready to save: the task's details as the visitor gave them."""

    task: str  # "message"
    details: dict[str, str]  # label → value, the name and phone left out
    name: str
    phone: str


def record_code(job: str, number: int) -> str:
    """What the visitor and the business see: the job's first letter and the number, "A-104"."""
    return f"{job[:1].upper()}-{number}"


CODE = re.compile(r"\s*([a-z])?\s*-?\s*(\d{1,9})\s*", re.IGNORECASE)


def parse_code(text: str) -> tuple[str, int] | None:
    """ "A-104", "a104", "a 104" or "104" → ("A", 104); the letter is "" when left out."""
    found = CODE.fullmatch(text)
    return ((found[1] or "").upper(), int(found[2])) if found else None


@dataclass(frozen=True)
class Appointment:
    """An appointment request, as the clinic's tools see it."""

    number: int
    name: str  # who it's under
    doctor: str
    time: str  # as shown: "Wed 7 Oct, 7:00 pm"
    status: str

    @property
    def code(self) -> str:
        return record_code("appointment", self.number)


@dataclass(frozen=True)
class AppointmentChange:
    """Cancel an appointment (`new_time` None), or move it to `new_time` (as shown)."""

    number: int
    new_time: str | None = None
