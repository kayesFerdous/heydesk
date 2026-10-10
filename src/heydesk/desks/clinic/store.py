"""A clinic's data: its doctors and appointments, plus what every desk has (`Store`)."""

from datetime import UTC

from sqlalchemy import delete, select

from heydesk.desks.base import Store
from heydesk.desks.clinic.models import Appointment, DoctorRow
from heydesk.desks.clinic.values import AppointmentChange, Doctor, NewAppointment
from heydesk.domain.values import Confirmed
from heydesk.errors import NotFound
from heydesk.models.taken import FIRST_STATUS, UNPAID


class ClinicStore(Store):
    async def doctors(self) -> list[Doctor]:
        async with self.sessions() as s:
            rows = await s.scalars(
                select(DoctorRow)
                .where(DoctorRow.workspace_id == self.workspace_id)
                .order_by(DoctorRow.position)
            )
            return [Doctor.model_validate(row, from_attributes=True) for row in rows]

    async def replace_doctors(self, doctors: list[Doctor]) -> None:
        """The clinic's doctors become exactly these, in this order."""
        async with self.sessions() as s, s.begin():
            await s.execute(delete(DoctorRow).where(DoctorRow.workspace_id == self.workspace_id))
            s.add_all(
                DoctorRow(workspace_id=self.workspace_id, position=n, **doctor.model_dump())
                for n, doctor in enumerate(doctors)
            )

    async def save_appointment(self, appointment: Confirmed[NewAppointment]) -> Appointment:
        """Saves the request: `new`, or `unpaid` when a deposit is paid first (D48)."""
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
        )

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
