"""A clinic's own routes: its doctors, and the appointment requests the agent took."""

from datetime import datetime
from typing import Annotated, cast

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict

from heydesk.api.deps import (
    EXPORT_ROWS,
    ImportIn,
    ImportOut,
    Paged,
    StatusIn,
    SuggestIn,
    Svc,
    Workspace,
    amount_cell,
    csv_file,
    phone_cell,
)
from heydesk.desks.clinic.models import Appointment
from heydesk.desks.clinic.store import ClinicStore
from heydesk.desks.clinic.values import DOCTOR_COLUMNS, Doctor, doctors_from_csv
from heydesk.desks.csv_import import Suggestion, suggest
from heydesk.runtime.speech import local_time

router = APIRouter(tags=["clinic"])


async def clinic_store(services: Svc, ws: Workspace) -> ClinicStore:
    """404 unless the business's agent is a clinic."""
    return cast(ClinicStore, await services.agents.store(ws, "clinic"))


type Clinic = Annotated[ClinicStore, Depends(clinic_store)]


class AppointmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    conversation_id: str | None
    code: str  # what the patient was told: "A-104"
    status: str  # new → confirmed → done, or cancelled
    name: str
    phone: str
    doctor_code: str
    doctor_name: str
    at: datetime  # UTC
    deposit: int  # the clinic's when it was booked; minor units
    currency: str
    paid: int  # paid online first
    payment_ref: str  # the payment gateway's id for it
    trx_id: str  # a deposit sent by hand: its bKash or Nagad TrxID; "" none
    trx_checked: bool  # the clinic found it in its account
    created_at: datetime
    updated_at: datetime


@router.get("/api/doctors")
async def doctors(store: Clinic) -> list[Doctor]:
    return await store.doctors()


@router.put("/api/doctors")
async def replace_doctors(body: list[Doctor], store: Clinic) -> list[Doctor]:
    """The clinic's doctors become exactly these, in this order. Live from the next message.
    A 409 while a Google Sheet is the list."""
    await store.not_linked()
    await store.replace_doctors(body)
    return body


@router.get("/api/appointments")
async def appointments(
    store: Clinic, page: Paged, status: str | None = None, q: str | None = None
) -> list[AppointmentOut]:
    """Newest first, a page at a time (`limit`, `before`). `q` matches the code ("A-104"),
    the name or the phone."""
    found = await store.find(Appointment, status, q, limit=page.limit, before=page.before)
    return [AppointmentOut.model_validate(a) for a in found]


@router.get("/api/appointments.csv", response_model=None)
async def appointments_csv(
    store: Clinic, services: Svc, ws: Workspace, status: str | None = None, q: str | None = None
) -> Response:
    """The appointments the list shows, as a spreadsheet's file."""
    tz = (await services.agents.by_workspace(ws)).config.timezone
    rows = [
        [
            a.code,
            local_time(a.at, tz),
            a.doctor_name,
            a.status,
            a.name,
            phone_cell(a.phone),
            amount_cell(a.paid),
        ]
        for a in await store.find(Appointment, status, q, limit=EXPORT_ROWS)
    ]
    columns = ["Appointment", "At", "Doctor", "Status", "Name", "Mobile", "Paid online"]
    return csv_file("appointments", columns, rows)


@router.get("/api/appointments/{appointment_id}")
async def appointment(appointment_id: str, store: Clinic) -> AppointmentOut:
    return AppointmentOut.model_validate(await store.get(Appointment, appointment_id))


@router.patch("/api/appointments/{appointment_id}")
async def set_status(appointment_id: str, body: StatusIn, store: Clinic) -> AppointmentOut:
    found = await store.set_status(Appointment, appointment_id, body.status)
    return AppointmentOut.model_validate(found)


class TrxIn(BaseModel):
    checked: bool


@router.put("/api/appointments/{appointment_id}/trx")
async def trx_checked(appointment_id: str, body: TrxIn, store: Clinic) -> AppointmentOut:
    """The clinic ticks the deposit's TrxID as found in its bKash or Nagad account."""
    return AppointmentOut.model_validate(await store.trx_checked(appointment_id, body.checked))


@router.post("/api/doctors/import/suggest")
async def suggest_doctors_columns(body: SuggestIn, _: Clinic) -> Suggestion:
    """The file's columns, a few rows and our fields, with a guess at which column is which
    field. The owner checks it, then imports with that `mapping`."""
    return suggest(body.csv, DOCTOR_COLUMNS)


@router.post("/api/doctors/import")
async def import_doctors(body: ImportIn, store: Clinic) -> ImportOut[Doctor]:
    """Replaces the doctors with the CSV's rows. Problems come back per row, and nothing is
    saved until the whole file is clean. `dry_run` only checks it."""
    await store.not_linked()
    found = doctors_from_csv(body.csv, body.mapping)
    if not body.dry_run:
        await store.replace_doctors(found)
    return ImportOut[Doctor](saved=not body.dry_run, count=len(found), preview=found[:5])
