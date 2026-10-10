"""The clinic's tools: find a doctor; request, cancel or move an appointment."""

from datetime import datetime
from typing import cast
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from heydesk.desks.clinic.models import Appointment
from heydesk.desks.clinic.store import ClinicStore
from heydesk.desks.clinic.values import (
    AppointmentChange,
    ClinicConfig,
    NewAppointment,
    match_doctor,
)
from heydesk.desks.shared import awaiting_payment
from heydesk.domain.values import parse_code
from heydesk.runtime import parse
from heydesk.runtime.guards import ask_for_yes, confirm
from heydesk.runtime.schedule import DAYS
from heydesk.runtime.speech import money
from heydesk.runtime.tool import Checks, Tool, ToolContext, ToolResult, fail

type Ctx = ToolContext[ClinicStore]


class FindDoctorInput(BaseModel):
    speciality: str = Field(default="", description="A speciality from the list, like Cardiology")
    day: str = Field(default="", description="today, tomorrow, or a weekday like saturday")
    language: str = Field(default="", description="A language the doctor speaks, like bangla")


async def find_doctor(ctx: Ctx, args: FindDoctorInput) -> ToolResult:
    doctors = [d for d in await ctx.store.doctors() if d.available]
    data: dict[str, object] = {}
    if speciality := args.speciality.strip().lower():
        doctors = [d for d in doctors if speciality in d.speciality.lower()]
    if language := args.language.strip().lower():
        doctors = [d for d in doctors if language in (lang.lower() for lang in d.languages)]
    if args.day.strip():
        try:
            day = parse.day_of(args.day, ctx.local_now)
        except ValueError as error:
            return fail(str(error))
        doctors = [d for d in doctors if DAYS[day.weekday()] in d.days]
        data["day"] = f"{day:%A} {day.day} {day:%b}"  # Python says which date it is
    data["doctors"] = [d.line(ctx.agent.currency) for d in doctors]
    if not doctors:
        data["hint"] = "No doctor fits all of that. Offer another day or speciality."
    return ToolResult(ok=True, data=data)


FIND_DOCTOR = Tool(
    name="find_doctor",
    description=(
        "Find the doctors who fit: a `speciality`, a `day` (today, tomorrow, a weekday) and a "
        "`language`, any of them. Use it to say who sees patients on a day: never work out "
        "the day yourself. Returns each doctor's days, hours and fee."
    ),
    input_model=FindDoctorInput,
    run=find_doctor,
    label="Checking the doctors…",
)


class RequestAppointmentInput(BaseModel):
    doctor: str = Field(default="", description="The doctor's code or name, like dr-akter")
    time: str = Field(default="", description="The day and time, like tomorrow at 5 pm")
    name: str = Field(default="", description="The patient's name")
    phone: str = Field(default="", description="The number the clinic calls to confirm")


async def request_appointment(ctx: Ctx, args: RequestAppointmentInput) -> ToolResult:
    doctors = await ctx.store.doctors()
    check = Checks()
    doctor = check.take("doctor", args.doctor, lambda said: match_doctor(doctors, said))
    when = check.take(
        "time", args.time, lambda said: parse.day_and_time(said, ctx.local_now, ctx.agent.hours)
    )
    if doctor and when and (problem := doctor.works_at(when)):
        check.problems["time"] = problem  # the doctor doesn't see patients then
        when = None
    name = check.take("name", args.name, parse.name)
    phone = check.take("phone", args.phone, parse.phone)
    if doctor is None or when is None or name is None or phone is None:
        return check.result()

    config = cast(ClinicConfig, ctx.agent)
    deposit = config.deposit if config.pay_first else 0
    appointment = NewAppointment(
        doctor, when, name, phone, config.currency, deposit, pay_first=deposit > 0
    )
    amount = money(deposit, config.currency)
    read_back = f"{doctor.name} on {parse.day_time(when)}, for {name}, phone {phone}" + (
        f"; a deposit of {amount} paid online now" if deposit else ""
    )
    confirmed = await confirm(ctx.decider, appointment, read_back, ctx.agent_said, ctx.visitor_said)
    if confirmed is None:
        return ask_for_yes(read_back, "request_appointment")
    saved = await ctx.store.save_appointment(confirmed)
    code = saved.code
    if appointment.pay_first:  # the clinic hears about it once it's paid (D48)
        return awaiting_payment(code, ctx.store.pay_link(saved.id), amount, saved.PAY_FOR)
    return ToolResult(
        ok=True,
        data={
            "code": code,
            "next": f"Tell them the clinic will call to confirm, and give them the code {code}: "
            "they need it to change or cancel.",
        },
    )


REQUEST_APPOINTMENT = Tool(
    name="request_appointment",
    description=(
        "Request an appointment: the doctor, the day and time, the patient's name and phone. "
        "Call it as soon as you have the doctor and the time: it checks the doctor works then "
        "and says what's still needed. When it has everything it gives you what to read back; "
        "call it again with the same details after a clear yes. If the clinic takes a "
        "deposit first, it returns a payment link."
    ),
    input_model=RequestAppointmentInput,
    run=request_appointment,
    label="Checking the doctor's times…",
    timeout_s=10.0,  # the commit check is a network call
)


class CancelInput(BaseModel):
    code: str = Field(description="The appointment code the visitor gave, like A-104")
    name: str = Field(description="The name the visitor says the appointment is under")


class RescheduleInput(CancelInput):
    new_time: str = Field(description="The new day and time, like tomorrow at 7 pm")


def _same_name(a: str, b: str) -> bool:
    return " ".join(a.casefold().split()) == " ".join(b.casefold().split())


async def _find(ctx: Ctx, code: str, name: str) -> Appointment | ToolResult:
    """The appointment, if the code and the name both match. One answer for a wrong code and
    a wrong name, so a stranger can't find out whose appointment a code is."""
    parsed = parse_code(code)
    if parsed is None or parsed[0] not in ("", "A"):
        return fail("That isn't an appointment code: they look like A-104. Ask again.")
    found = await ctx.store.appointment(parsed[1])
    if found is None or not _same_name(found.name, name):
        return fail("No appointment matches that code and name. Ask them to check both.")
    if found.status == "cancelled":
        return fail(f"Appointment {found.code} is already cancelled.")
    return found


def _local(ctx: Ctx, at: datetime) -> datetime:
    return at.astimezone(ZoneInfo(ctx.agent.timezone))


async def _change(ctx: Ctx, change: AppointmentChange, read_back: str, tool: str) -> ToolResult:
    """The commit check (rule 2): the change is made only after a read-back and a yes."""
    confirmed = await confirm(ctx.decider, change, read_back, ctx.agent_said, ctx.visitor_said)
    if confirmed is None:
        return ask_for_yes(read_back, tool)
    await ctx.store.change_appointment(confirmed)
    return ToolResult(ok=True, data={"done": read_back})


async def cancel_appointment(ctx: Ctx, args: CancelInput) -> ToolResult:
    found = await _find(ctx, args.code, args.name)
    if isinstance(found, ToolResult):
        return found
    when = parse.day_time(_local(ctx, found.at))
    read_back = f"Cancel appointment {found.code} with {found.doctor_name} on {when}"
    return await _change(ctx, AppointmentChange(found.number), read_back, "cancel_appointment")


async def reschedule_appointment(ctx: Ctx, args: RescheduleInput) -> ToolResult:
    found = await _find(ctx, args.code, args.name)
    if isinstance(found, ToolResult):
        return found
    try:
        when = parse.day_and_time(args.new_time, ctx.local_now, ctx.agent.hours)
        doctor = match_doctor(await ctx.store.doctors(), found.doctor_code)
    except ValueError as error:
        return fail(str(error))
    if problem := doctor.works_at(when):
        return fail(problem)
    read_back = f"Move appointment {found.code} with {found.doctor_name} to {parse.day_time(when)}"
    change = AppointmentChange(found.number, when)
    return await _change(ctx, change, read_back, "reschedule_appointment")


CANCEL_APPOINTMENT = Tool(
    name="cancel_appointment",
    description=(
        "Cancel an appointment. First ask for its code (like A-104), then the name it's under. "
        "The first call finds it and gives what to read back; call again after a clear yes."
    ),
    input_model=CancelInput,
    run=cancel_appointment,
    label="Finding your appointment…",
    timeout_s=10.0,
)

RESCHEDULE_APPOINTMENT = Tool(
    name="reschedule_appointment",
    description=(
        "Move an appointment to a new day and time. First ask for its code (like A-104), then "
        "the name it's under, then the new time. The first call checks the doctor works then "
        "and gives what to read back; call again after a clear yes."
    ),
    input_model=RescheduleInput,
    run=reschedule_appointment,
    label="Finding your appointment…",
    timeout_s=10.0,
)

TOOLS = [FIND_DOCTOR, REQUEST_APPOINTMENT, CANCEL_APPOINTMENT, RESCHEDULE_APPOINTMENT]
