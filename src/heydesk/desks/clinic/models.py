from datetime import datetime, time
from typing import Any, ClassVar

from sqlalchemy import JSON, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from heydesk.db import Base, new_id
from heydesk.models.taken import Taken


class DoctorRow(Base):
    """One of a clinic's doctors (`values.Doctor`)."""

    __tablename__ = "doctors"
    __table_args__ = (UniqueConstraint("workspace_id", "code"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    code: Mapped[str] = mapped_column(String(50))
    name: Mapped[str] = mapped_column(String(200))
    speciality: Mapped[str] = mapped_column(String(100))
    fee: Mapped[int]  # minor units
    days: Mapped[list[Any]] = mapped_column(JSON)  # ["sat", "sun"]
    start: Mapped[time]
    end: Mapped[time]
    languages: Mapped[list[Any]] = mapped_column(JSON, default=list)
    description: Mapped[str] = mapped_column(default="")
    available: Mapped[bool] = mapped_column(default=True)
    position: Mapped[int] = mapped_column(default=0)  # the clinic's order, for the prompt


class Appointment(Taken, Base):
    """An appointment request: the clinic phones the patient to confirm it."""

    __tablename__ = "appointments"
    __table_args__ = (UniqueConstraint("workspace_id", "number"),)
    LETTER: ClassVar[str] = "A"
    FLOW: ClassVar[dict[str, set[str]]] = {
        "new": {"confirmed", "cancelled"},
        "confirmed": {"done", "cancelled", "new"},  # moved to a new time: to confirm again
        "done": set(),
        "cancelled": set(),
    }

    doctor_code: Mapped[str] = mapped_column(String(50))
    doctor_name: Mapped[str] = mapped_column(String(200))  # as it was when booked
    at: Mapped[datetime]  # UTC
