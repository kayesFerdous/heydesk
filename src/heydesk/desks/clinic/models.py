from datetime import datetime, time
from typing import Any, ClassVar

from sqlalchemy import JSON, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from heydesk.db import Base, new_id
from heydesk.models.customer_request import CustomerRequest, Payable


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


class Appointment(CustomerRequest, Payable, Base):
    """An appointment request: the clinic phones the patient to confirm it.

    When the clinic takes a deposit first, the request starts `unpaid` and only the payment
    check makes it `new`."""

    __tablename__ = "appointments"
    __table_args__ = (UniqueConstraint("workspace_id", "number"),)
    LETTER: ClassVar[str] = "A"
    PAY_FOR: ClassVar[str] = "the deposit"
    FLOW: ClassVar[dict[str, set[str]]] = {
        "unpaid": {"new", "cancelled"},  # paid online; or the owner took it another way
        "new": {"confirmed", "cancelled"},
        "confirmed": {"done", "cancelled", "new"},  # moved to a new time: to confirm again
        "done": set(),
        "cancelled": set(),
    }

    doctor_code: Mapped[str] = mapped_column(String(50))
    doctor_name: Mapped[str] = mapped_column(String(200))  # as it was when booked
    at: Mapped[datetime]  # UTC
    deposit: Mapped[int] = mapped_column(default=0)  # minor units, the clinic's when booked
    # A deposit sent by hand: the bKash or Nagad TrxID the patient typed ("" none),
    # and whether the clinic ticked it as found in its account
    trx_id: Mapped[str] = mapped_column(String(30), default="", server_default="")
    trx_checked: Mapped[bool] = mapped_column(default=False, server_default="0")
    reminded: Mapped[bool] = mapped_column(default=False, server_default="0")  # the day before

    @property
    def amount_due(self) -> int:
        return self.deposit

    def about(self) -> str:
        return f"with {self.doctor_name}"

    def after(self) -> str:
        return f"Your appointment request {self.code} is in: the clinic will call to confirm it."
