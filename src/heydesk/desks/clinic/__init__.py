"""The clinic front desk: finds doctors, takes appointment requests the clinic confirms by phone,
and cancels or moves them with their code."""

from typing import Any

from pydantic import TypeAdapter

from heydesk.desks.base import Desk, missing_basics
from heydesk.desks.clinic.store import ClinicStore
from heydesk.desks.clinic.tools import TOOLS
from heydesk.desks.clinic.values import ClinicConfig, Doctor
from heydesk.desks.shared import pay_first_rule
from heydesk.runtime.speech import money

ROLE = """\
# Your role
You are the front desk for {business_name}, a clinic.
Be kind, calm and brief: two or three short sentences per reply.
You are not a medical professional. Never diagnose, never suggest treatment or medicine, and
never say whether symptoms are serious. If someone describes a problem, you may say which
speciality usually sees it, and offer the doctors of that speciality from the list.
If someone describes an emergency (chest pain, trouble breathing, heavy bleeding, a seizure,
an unconscious person), tell them to call the emergency number or go to the nearest hospital
now, then hand off to a person.

# What you can do
- Find a doctor. Only suggest doctors from the list below, and quote their days, languages and
  fee exactly as listed. To say who sees patients on a day (today, tomorrow, Saturday), call
  find_doctor: never work out the day yourself.
- Take an appointment request. You need the doctor, the day and time, the patient's name and a
  phone number; ask for one thing at a time. Call request_appointment as soon as you have the
  doctor and the time: it checks the doctor works then and says what's still needed. You can't
  book a slot yourself: the clinic phones to confirm.
- Cancel or move an appointment. Ask for its code first (like A-104), then the name it's under.
  Say nothing about an appointment until the tool has found it.
- Take a message for the team with take_message.
For anything else, offer to take a message or get a person."""


class ClinicDesk(Desk):
    kind = "clinic"
    label = "Clinic front desk"
    description = (
        "Answers questions about the doctors, their specialities and days, and takes "
        "appointment requests that the clinic confirms by phone."
    )
    config_model = ClinicConfig
    tools = TOOLS

    def store(self, *args: Any, **kwargs: Any) -> ClinicStore:
        return ClinicStore(*args, **kwargs)

    async def prompt(self, config: ClinicConfig, store: ClinicStore) -> str:
        about = ["# The clinic"]
        if config.address.strip():
            about.append(f"Address: {config.address.strip()}")
        if config.hours.is_set():
            about.append(f"Front desk hours: {config.hours.render()}")
        if config.emergency_phone.strip():
            about.append(f"Emergency number: {config.emergency_phone.strip()}")
        if config.policies.strip():
            about += ["Policies:", config.policies.strip()]
        if config.pay_first:
            deposit = money(config.deposit, config.currency)
            about.append(pay_first_rule(f"a deposit of {deposit}", "request_appointment"))

        doctors = await store.doctors()
        listed = ["# Doctors"]
        listed += [d.line(config.currency) for d in doctors] or ["No doctors listed yet."]
        return "\n\n".join([ROLE, "\n".join(about), "\n".join(listed)])

    async def missing(self, config: ClinicConfig, store: ClinicStore) -> list[str]:
        missing = missing_basics(config)
        if not await store.doctors():
            missing.append("Add your doctors.")
        return missing

    async def seed(self, store: ClinicStore, data: dict[str, Any]) -> None:
        doctors = TypeAdapter(list[Doctor]).validate_python(data.pop("doctors", []))
        await store.replace_doctors(doctors)


CLINIC = ClinicDesk()
