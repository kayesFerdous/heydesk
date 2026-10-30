"""A clinic's data: its doctors and appointments, plus what every desk has (`Store`)."""

from datetime import UTC
from typing import Any

from sqlalchemy import select

from heydesk.desks.base import Store
from heydesk.desks.clinic.models import Appointment, DoctorRow
from heydesk.desks.clinic.values import (
    AppointmentChange,
    Doctor,
    NewAppointment,
    doctors_from_csv,
)
from heydesk.domain.values import Confirmed
from heydesk.errors import NotFound
from heydesk.models.customer_request import FIRST_STATUS, UNPAID


class ClinicStore(Store):
    SALES = (Appointment,)

    async def doctors(self) -> list[Doctor]:
        return await self.rows_of(DoctorRow, Doctor)

    async def replace_doctors(self, doctors: list[Doctor]) -> None:
        await self.replace_rows(DoctorRow, doctors)

    async def take_sheet(
        self, text: str, mapping: dict[str, str], seen: dict[str, Any]
    ) -> dict[str, Any]:
        await self.replace_doctors(doctors_from_csv(text, mapping))
        return {}

    async def save_appointment(self, appointment: Confirmed[NewAppointment]) -> Appointment:
        """Saves the request: `new`, or `unpaid` when a deposit is paid first."""
        a = appointment.value
        return await self.add(
            Appointment,
            status=UNPAID if a.pay_first else FIRST_STATUS,
            name=a.name,
            phone=a.phone,
            doctor_code=a.doctor.code,
            doctor_name=a.doctor.name,
            at=a.at.astimezone(UTC),
            deposit=a.deposit,
            currency=a.currency,
            trx_id=a.trx_id,
        )

    async def trx_checked(self, appointment_id: str, checked: bool) -> Appointment:
        """The clinic found the deposit's TrxID in its bKash or Nagad account, or unticks it."""
        async with self.sessions() as s, s.begin():
            found = await self._one(s, Appointment, appointment_id)
            found.trx_checked = checked
            found.updated_at = self.now()
        return found

    async def appointment(self, number: int) -> Appointment | None:
        return await self.by_number(Appointment, number)

    async def change_appointment(self, change: Confirmed[AppointmentChange]) -> None:
        """Cancels it, or moves it to the new time; a moved one is new again, for the clinic
        to confirm by phone."""
        c = change.value
        async with self.sessions() as s, s.begin():
            found = await s.scalar(
                select(Appointment).where(
                    Appointment.workspace_id == self.workspace_id, Appointment.number == c.number
                )
            )
            if found is None:
                raise NotFound("There's no such appointment.")
            if c.new_at is None:
                found.move("cancelled", self.now())
                return
            found.at = c.new_at.astimezone(UTC)
            if found.status not in (FIRST_STATUS, UNPAID):  # moving it doesn't pay for it
                found.move(FIRST_STATUS, self.now())
            found.updated_at = self.now()
