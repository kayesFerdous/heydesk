"""The clinic's own tools (D37, D38): find a doctor; cancel or move an appointment."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from heydesk.domain.values import Appointment, AppointmentChange, parse_code
from heydesk.runtime.doctors import doctor_problem, match_doctor
from heydesk.runtime.guards import confirm
from heydesk.runtime.items import Filter, ItemQuery, find_items, item_line
from heydesk.runtime.schedule import DAYS, WeeklyHours
from heydesk.runtime.tasks import WEEKDAYS, display_detail, parse_appointment_time
from heydesk.runtime.tool import Tool, ToolContext, ToolResult, fail


class FindDoctorInput(BaseModel):
    speciality: str = Field(default="", description="A speciality from the list, like Cardiology")
    day: str = Field(default="", description="today, tomorrow, or a weekday like saturday")
    language: str = Field(default="", description="A language the doctor speaks, like bangla")


async def find_doctor(ctx: ToolContext, args: FindDoctorInput) -> ToolResult:
    filters = []
    data: dict[str, object] = {}
    if args.day.strip():
        today = ctx.now.astimezone(ZoneInfo(ctx.agent.timezone)).date()
        day = args.day.strip().lower()
        if day == "today":
            date = today
        elif day == "tomorrow":
            date = today + timedelta(days=1)
        elif day in WEEKDAYS:
            date = today + timedelta(days=(WEEKDAYS[day] - today.weekday()) % 7)
        else:
            return fail("Pass the day as today, tomorrow or a weekday like saturday.")
        filters.append(Filter(field="days", op="includes", value=DAYS[date.weekday()]))
        data["day"] = f"{date:%A} {date.day} {date:%b}"  # Python says which date it is
    if args.language.strip():
        filters.append(Filter(field="languages", op="includes", value=args.language.strip()))
    query = ItemQuery(category=args.speciality.strip(), filters=filters)
    found = find_items(await ctx.items.all(), query)
    data["doctors"] = [item_line(d, ctx.agent.currency) for d in found]
    if not found:
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


class CancelInput(BaseModel):
    code: str = Field(description="The appointment code the visitor gave, like A-104")
    name: str = Field(description="The name the visitor says the appointment is under")


class RescheduleInput(CancelInput):
    new_time: str = Field(description="The new day and time, like tomorrow at 7 pm")


def _same_name(a: str, b: str) -> bool:
    return " ".join(a.casefold().split()) == " ".join(b.casefold().split())


async def _find(ctx: ToolContext, code: str, name: str) -> Appointment | ToolResult:
    """The appointment, if the code and the name both match. One answer for a wrong code and
    a wrong name, so a stranger can't find out whose appointment a code is."""
    parsed = parse_code(code)
    if parsed is None or parsed[0] not in ("", "A"):
        return fail("That isn't an appointment code: they look like A-104. Ask again.")
    found = await ctx.appointments.find(parsed[1])
    if found is None or not _same_name(found.name, name):
        return fail("No appointment matches that code and name. Ask them to check both.")
    if found.status == "cancelled":
        return fail(f"Appointment {found.code} is already cancelled.")
    return found


async def _change(ctx: ToolContext, change: AppointmentChange, read_back: str) -> ToolResult:
    """The commit check (rule 2): the change is made only after a read-back and a yes."""
    confirmed = await confirm(ctx.decider, change, read_back, ctx.agent_said, ctx.visitor_said)
    if confirmed is None:
        return ToolResult(
            ok=False,
            data={
                "read_back": read_back,
                "next": "Read this back and ask for a clear yes. Call again only after the yes.",
            },
        )
    await ctx.appointments.change(confirmed)
    return ToolResult(ok=True, data={"done": read_back})


async def cancel_appointment(ctx: ToolContext, args: CancelInput) -> ToolResult:
    found = await _find(ctx, args.code, args.name)
    if isinstance(found, ToolResult):
        return found
    read_back = f"Cancel appointment {found.code} with {found.doctor} on {found.time}"
    return await _change(ctx, AppointmentChange(found.number), read_back)


async def reschedule_appointment(ctx: ToolContext, args: RescheduleInput) -> ToolResult:
    found = await _find(ctx, args.code, args.name)
    if isinstance(found, ToolResult):
        return found
    now = ctx.now.astimezone(ZoneInfo(ctx.agent.timezone))
    hours = WeeklyHours.model_validate(ctx.agent.knowledge.get("hours") or {})
    try:
        when = parse_appointment_time(args.new_time, now, hours)
    except ValueError as error:
        return fail(str(error))
    doctor = match_doctor(await ctx.items.all(), found.doctor)
    problem = doctor_problem(doctor, datetime.fromisoformat(when))
    if problem:
        return fail(problem)
    new_time = display_detail("appointment_time", when, ctx.agent.currency)
    read_back = f"Move appointment {found.code} with {found.doctor} to {new_time}"
    return await _change(ctx, AppointmentChange(found.number, new_time), read_back)


CANCEL_APPOINTMENT = Tool(
    name="cancel_appointment",
    description=(
        "Cancel an appointment. First ask for its code (like A-104), then the name it's under. "
        "The first call finds it and gives what to read back; call again after a clear yes."
    ),
    input_model=CancelInput,
    run=cancel_appointment,
    label="Finding your appointment…",
    timeout_s=10.0,  # the commit check is a network call
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
