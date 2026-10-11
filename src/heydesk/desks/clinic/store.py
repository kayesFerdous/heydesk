"""A clinic's data: its doctors and appointments, plus what every desk has (`Store`)."""

from datetime import UTC

from sqlalchemy import select

from heydesk.desks.base import Store
from heydesk.desks.clinic.models import Appointment, DoctorRow
from heydesk.desks.clinic.values import AppointmentChange, Doctor, NewAppointment
from heydesk.domain.values import Confirmed
from heydesk.errors import NotFound
from heydesk.models.taken import FIRST_STATUS


class ClinicStore(Store):
    async def doctors(self) -> list[Doctor]:
        return await self.rows_of(DoctorRow, Doctor)

    async def replace_doctors(self, doctors: list[Doctor]) -> None:
        await self.replace_rows(DoctorRow, doctors)

    async def save_appointment(self, appointment: Confirmed[NewAppointment]) -> str:
        """Saves the request and returns its code: "A-104"."""
        a = appointment.value
        saved = await self.add(
            Appointment,
            name=a.name,
            phone=a.phone,
            doctor_code=a.doctor.code,
            doctor_name=a.doctor.name,
            at=a.at.astimezone(UTC),
        )
        return saved.code

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
            if found.status != FIRST_STATUS:
                found.move(FIRST_STATUS, self.now())
            found.updated_at = self.now()
