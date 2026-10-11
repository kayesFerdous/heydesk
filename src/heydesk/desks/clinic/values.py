"""A clinic's doctors and appointments, as its tools, prompt and routes see them. Pure."""

import re
from dataclasses import dataclass
from datetime import datetime, time
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from heydesk.desks import csv_import
from heydesk.runtime.config import AgentConfig
from heydesk.runtime.schedule import DAYS
from heydesk.runtime.speech import clock_time, money

type Day = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


class Doctor(BaseModel):
    code: str = Field(min_length=1, max_length=50)  # the short id the model uses: "dr-akter"
    name: str = Field(min_length=1, max_length=200)
    speciality: str
    fee: int = Field(ge=0)  # minor units (paisa), so 80000 is ৳800
    days: list[Day]  # when they see patients
    start: time  # "17:00"
    end: time  # "21:00"
    languages: list[str] = []
    description: str = ""
    available: bool = True

    @field_validator("days", mode="before")
    @classmethod
    def _short_days(cls, days: object) -> object:
        """ "Saturday" or "Sat" → "sat"."""
        if isinstance(days, list):
            return [str(day).lower()[:3] for day in days]
        return days

    @model_validator(mode="after")
    def _start_before_end(self) -> "Doctor":
        if self.end <= self.start:
            raise ValueError("end must be after start")
        return self

    def works_at(self, when: datetime) -> str | None:
        """Why this doctor can't see patients at `when` (local time), in words for the model;
        None if they can."""
        if DAYS[when.weekday()] not in self.days:
            return (
                f"{self.name} sees patients on {self.day_names()}, not on {when:%A}s. "
                "Ask for one of those days."
            )
        if not self.start <= when.time() < self.end:
            return (
                f"{self.name} sees patients from {clock_time(self.start)} to "
                f"{clock_time(self.end)}. Ask for a time then."
            )
        return None

    def day_names(self) -> str:
        return ", ".join(day.title() for day in self.days)

    def line(self, currency: str) -> str:
        """One doctor as a prompt line."""
        line = f"- {self.name} [{self.code}], {self.speciality}, fee {money(self.fee, currency)}"
        if self.description:
            line += f": {self.description}"
        line += (
            f" Sees patients {self.day_names()}, {clock_time(self.start)}–{clock_time(self.end)}."
        )
        if self.languages:
            line += f" Speaks {', '.join(lang.title() for lang in self.languages)}."
        if not self.available:
            line += " NOT AVAILABLE"
        return line


def match_doctor(doctors: list[Doctor], said: str) -> Doctor:
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


@dataclass(frozen=True)
class NewAppointment:
    """An appointment request, ready to save once the visitor said yes. `at` is local time."""

    doctor: Doctor
    at: datetime
    name: str
    phone: str
    currency: str
    deposit: int = 0  # minor units
    pay_first: bool = False  # saved as unpaid until the deposit is paid online (D48)


@dataclass(frozen=True)
class AppointmentChange:
    """Cancel an appointment (`new_at` None), or move it to `new_at` (local time)."""

    number: int
    new_at: datetime | None = None


def doctors_from_csv(text: str) -> list[Doctor]:
    """Columns: name, speciality, fee, days ("sat; sun; mon"), and either start and end
    ("17:00", "21:00") or hours ("17:00-21:00"); optionally languages, description,
    available (yes/no), code."""

    def row(cells: dict[str, str]) -> Doctor:
        start, end = cells.get("start", ""), cells.get("end", "")
        if not (start and end):
            start, _, end = cells.get("hours", "").partition("-")
        return Doctor.model_validate(
            {
                "code": cells.get("code") or csv_import.slug(cells["name"]),
                "name": cells["name"],
                "speciality": cells["speciality"],
                "fee": csv_import.money(cells["fee"]),
                "days": csv_import.items(cells["days"]),
                "start": start.strip(),
                "end": end.strip(),
                "languages": csv_import.items(cells.get("languages", "")),
                "description": cells.get("description", ""),
                "available": csv_import.available(cells.get("available", "")),
            }
        )

    return csv_import.parse(text, {"name", "speciality", "fee", "days"}, row)


class ClinicConfig(AgentConfig):
    greeting: str = (
        "Hello, thanks for contacting {business_name}. "
        "I can help you find a doctor or request an appointment."
    )
    emergency_phone: str = ""  # where to send someone who needs help now
    policies: str = ""  # insurance, what to bring, how fees are paid
    deposit: int = Field(default=0, ge=0)  # minor units, taken online with each request
    # The patient pays the deposit online first (SSLCommerz). The request reaches the clinic
    # only when the payment comes in (D48)
    pay_first: bool = False

    @model_validator(mode="after")
    def _something_to_pay_first(self) -> "ClinicConfig":
        if self.pay_first and not self.deposit:
            raise ValueError("To take a deposit first, set how much it is.")
        return self
