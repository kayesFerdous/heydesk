"""The clinic desk: doctors with their own days and hours, an appointment request saved only
after a yes, and cancelling or moving it with its code and name."""

from typing import Any

from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select

from heydesk.desks.clinic import CLINIC
from heydesk.desks.clinic.models import Appointment
from heydesk.desks.clinic.tools import (
    CancelInput,
    FindDoctorInput,
    RequestAppointmentInput,
    RescheduleInput,
    cancel_appointment,
    find_doctor,
    request_appointment,
    reschedule_appointment,
)
from heydesk.ports.llm import ToolCall
from heydesk.services.agents import LiveAgent
from tests.conftest import SEEDS, World, make_ctx, said_yes

DETAILS = {
    "doctor": "Dr. Hasan",  # Tue and Wed, 18:00-21:00; tomorrow is Wed 30 Sep
    "time": "tomorrow at 7 pm",
    "name": "Kayes",
    "phone": "01712345678",
}
READ_BACK = "An appointment with Dr. Hasan, Wed 30 Sep at 7:00 pm, for Kayes. Shall I send it?"


def request(**changes: str) -> ToolCall:
    return ToolCall("c", "request_appointment", DETAILS | changes)


async def appointments(world: World) -> list[Appointment]:
    async with world.sessions() as s:
        return list(await s.scalars(select(Appointment)))


async def book(world: World, agent: LiveAgent) -> Appointment:
    conv = await world.say(agent, None, [request(), READ_BACK])
    await world.say(agent, conv, [request(), "Sent."], "yes")
    [saved] = await appointments(world)
    return saved


async def test_the_doctors_are_in_the_prompt_with_their_days_and_hours(world: World) -> None:
    agent = await world.agent("clinic")
    text = await CLINIC.prompt(agent.config, world.store(agent))

    assert agent.greeting.startswith("Hello, thanks for contacting Lakeview Clinic.")
    assert "Emergency number: 999" in text
    assert (
        "- Dr. Nasrin Akter [dr-akter], Cardiology, fee ৳800: Heart and blood pressure. "
        "Sees adults. Sees patients Sat, Sun, Mon, 5:00 pm–9:00 pm. Speaks English, Bangla."
    ) in text
    assert [t.name for t in CLINIC.tools] == [
        "find_doctor",
        "request_appointment",
        "cancel_appointment",
        "reschedule_appointment",
    ]


async def test_an_appointment_request_is_saved_only_after_a_yes(world: World) -> None:
    agent = await world.agent("clinic")
    conv = await world.say(agent, None, [request(), READ_BACK], "Dr. Hasan tomorrow 7 pm…")
    assert "Dr. Imran Hasan on Wed 30 Sep, 7:00 pm, for Kayes" in await world.tool_said(conv)

    await world.say(agent, conv, [READ_BACK], "hmm, maybe")  # not a yes: the model asks again
    assert await appointments(world) == []

    await world.say(agent, conv, [request(), "Sent!"], "yes please")
    [saved] = await appointments(world)
    assert (saved.code, saved.name, saved.doctor_name) == ("A-101", "Kayes", "Dr. Imran Hasan")
    assert saved.at.isoformat() == "2026-09-30T13:00:00+00:00"  # 7 pm in Dhaka
    assert "give them the code A-101" in await world.tool_said(conv)


async def test_a_doctor_is_booked_only_when_they_see_patients(world: World) -> None:
    agent = await world.agent("clinic")

    async def problems(**changes: str) -> str:
        conv = await world.say(agent, None, [request(**changes), "Another time?"])
        return await world.tool_said(conv)

    assert "not on Wednesdays" in await problems(doctor="Akter", time="tomorrow at 6 pm")
    assert "from 6:00 pm to 9:00 pm" in await problems(doctor="dr-hasan", time="tomorrow at 5 pm")
    assert "Wednesdays we're open 09:00–21:00" in await problems(time="tomorrow at 11 pm")
    assert "isn't one of our doctors" in await problems(doctor="Dr. Who")
    missing = await problems(name="", phone="")
    assert '"still_needed": ["name", "phone"]' in missing
    assert await appointments(world) == []


async def test_find_doctor_works_out_the_day_in_python(world: World) -> None:
    agent = await world.agent("clinic")
    find = ToolCall("c1", "find_doctor", {"speciality": "cardio", "day": "tomorrow"})
    conv = await world.say(agent, None, [find, "Tomorrow, Dr. Hasan sees heart patients."])

    found = await world.tool_said(conv)
    assert "Wednesday 30 Sep" in found and "Dr. Imran Hasan" in found
    assert "Dr. Nasrin Akter" not in found  # she doesn't work on Wednesdays


async def test_an_appointment_is_cancelled_with_its_code_and_name_after_a_yes(
    world: World,
) -> None:
    agent = await world.agent("clinic")
    booked = await book(world, agent)

    def cancel(code: str, name: str) -> list[object]:
        return [ToolCall("c", "cancel_appointment", {"code": code, "name": name}), "…"]

    wrong = await world.say(agent, None, cancel("a101", "Someone Else"))
    assert "No appointment matches that code and name" in await world.tool_said(wrong)
    assert "Hasan" not in await world.tool_said(wrong)  # nothing about it leaks

    conv = await world.say(agent, None, cancel("a 101", "kayes"))  # no read-back yet
    assert "Cancel appointment A-101 with Dr. Imran Hasan on Wed 30 Sep, 7:00 pm" in (
        await world.tool_said(conv)
    )
    assert (await appointments(world))[0].status == "new"

    await world.say(agent, conv, cancel("A-101", "Kayes"), "yes")  # after the yes
    [cancelled] = await appointments(world)
    assert cancelled.id == booked.id and cancelled.status == "cancelled"


async def test_an_appointment_moves_only_to_a_time_the_doctor_works(world: World) -> None:
    agent = await world.agent("clinic")
    await book(world, agent)

    def move(when: str) -> list[object]:
        args = {"code": "A-101", "name": "Kayes", "new_time": when}
        return [ToolCall("c", "reschedule_appointment", args), "…"]

    conv = await world.say(agent, None, move("monday at 7 pm"))  # Dr. Hasan: Tue and Wed
    assert "not on Mondays" in await world.tool_said(conv)
    await world.say(agent, conv, move("tuesday at 8 pm"))  # read back first

    await world.say(agent, conv, move("tuesday at 8 pm"), "yes")
    [moved] = await appointments(world)
    assert moved.at.isoformat() == "2026-09-29T14:00:00+00:00"  # today, 8 pm in Dhaka


async def test_the_owner_sees_appointments_and_edits_doctors(
    app: FastAPI, client: AsyncClient
) -> None:
    services = app.state.services
    await services.agents.ensure_dev_agent("clinic", SEEDS / "clinic.yaml")
    assert len((await client.get("/api/doctors")).json()) == 10
    assert (await client.get("/api/menu")).status_code == 404  # a clinic has no menu

    doctor = {
        "code": "dr-new",
        "name": "Dr. New",
        "speciality": "Dentistry",
        "fee": 50000,
        "days": ["Saturday", "Sun"],
        "start": "10:00",
        "end": "14:00",
    }
    saved = await client.put("/api/doctors", json=[doctor])
    assert saved.status_code == 200 and saved.json()[0]["days"] == ["sat", "sun"]
    backwards = await client.put("/api/doctors", json=[doctor | {"end": "09:00"}])
    assert backwards.status_code == 422

    assert (await client.get("/api/appointments")).json() == []


async def test_doctors_come_from_a_csv_and_a_bad_row_saves_nothing(
    app: FastAPI, client: AsyncClient
) -> None:
    await app.state.services.agents.ensure_dev_agent("clinic", SEEDS / "clinic.yaml")
    csv = (
        "Name,Speciality,Fee (৳),Days,Hours,Languages\n"
        'Dr. Rina Das,Dentistry,"1,200","Sat; Sun",10:00-14:00,bangla|english\n'
    )
    checked = await client.post("/api/doctors/import", json={"csv": csv, "dry_run": True})
    assert checked.json()["count"] == 1 and len((await client.get("/api/doctors")).json()) == 10

    await client.post("/api/doctors/import", json={"csv": csv})
    [doctor] = (await client.get("/api/doctors")).json()
    assert (doctor["code"], doctor["fee"], doctor["days"]) == (
        "dr-rina-das",
        120000,
        ["sat", "sun"],
    )

    bad = await client.post("/api/doctors/import", json={"csv": csv + "Dr. X,ENT,lots,Mon,9-5,\n"})
    assert bad.status_code == 422 and "row 3" in bad.json()["error"]["fields"]
    assert len((await client.get("/api/doctors")).json()) == 1


async def test_the_owner_hears_each_request_move_and_cancel(world: World) -> None:
    agent = await world.agent("clinic")

    def ctx() -> Any:
        made = make_ctx(world.store(agent), agent=agent.config)
        return made

    asked = await said_yes(
        request_appointment, ctx(), RequestAppointmentInput.model_validate(DETAILS)
    )
    assert asked.alert and asked.alert.startswith("🩺 New appointment request A-101")
    who = {"code": "A-101", "name": "Kayes"}
    moved = await said_yes(
        reschedule_appointment,
        ctx(),
        RescheduleInput.model_validate(who | {"new_time": "tuesday at 8 pm"}),
    )
    assert moved.alert and moved.alert.startswith("✏️ Appointment A-101 moved")
    cancelled = await said_yes(cancel_appointment, ctx(), CancelInput.model_validate(who))
    assert cancelled.alert == "❌ Appointment A-101 cancelled"


async def test_find_doctor_shows_the_doctors_with_a_book_button(world: World) -> None:
    agent = await world.agent("clinic")
    ctx = make_ctx(world.store(agent), agent=agent.config)
    found = await find_doctor(ctx, FindDoctorInput(speciality="cardio", day="tomorrow"))

    assert found.card == {  # D110: Python's facts, the same as the model's lines
        "kind": "doctors",
        "data": {
            "doctors": [
                {
                    "name": "Dr. Imran Hasan",
                    "speciality": "Cardiology",
                    "days": "Tue, Wed",
                    "hours": "6:00 pm–9:00 pm",
                    "fee": {"minor": 90000, "currency": "BDT", "text": "৳900"},
                    "book": "Book with Dr. Imran Hasan",  # a tap sends these words (D99)
                }
            ]
        },
    }
    nobody = await find_doctor(ctx, FindDoctorInput(speciality="dentistry"))
    assert nobody.card is None


async def test_a_request_shows_a_summary_then_a_receipt(world: World) -> None:
    agent = await world.agent("clinic")
    ctx = make_ctx(world.store(agent), agent=agent.config)
    args = RequestAppointmentInput.model_validate(DETAILS)
    rows = [
        {"label": "Doctor", "value": "Dr. Imran Hasan, Cardiology"},
        {"label": "Time", "value": "Wed 30 Sep, 7:00 pm", "at": "2026-09-30T13:00:00+00:00"},
        {"label": "Patient", "value": "Kayes"},
        {"label": "Phone", "value": "01712345678"},
    ]

    asked = await request_appointment(ctx, args)
    assert asked.card == {
        "kind": "summary",
        "data": {"title": "Appointment request", "lines": [], "rows": rows},
    }
    assert asked.choices == ["Yes", "Change"]  # the buttons stay as they were (D99)

    saved = await said_yes(request_appointment, ctx, args)
    assert saved.card == {
        "kind": "receipt",
        "data": {
            "title": "Appointment requested",
            "number": "A-101",
            "status": "Requested: the clinic will call to confirm it",
            "rows": rows,
        },
    }


async def test_a_patient_back_from_the_same_browser_is_offered_their_details(
    world: World,
) -> None:
    """The browser's token is the web chat's and a call's contact (D114): a later conversation
    from it, typed or spoken, has the past appointment in the prompt; another browser's doesn't."""
    agent = await world.agent("clinic")
    talk = world.services.conversations

    async def say(conv: str | None, script: list[Any], channel: str, token: str) -> str:
        world.llm.script = script
        turn = await talk.start_turn(agent, conv, "…", channel, f"web:{token}", token)
        [_ async for _ in turn.events]
        return turn.conversation_id

    conv = await say(None, [request(), READ_BACK], "web", "browser-1")
    assert "# Their past appointments" not in world.llm.requests[-1].system
    await say(conv, [request(), "Sent."], "web", "browser-1")

    await say(None, ["Hello again!"], "voice", "browser-1")
    system = world.llm.requests[-1].system
    assert "- A-101: Dr. Imran Hasan, for Kayes, phone 01712345678" in system
    await say(None, ["Hello!"], "web", "browser-2")
    assert "# Their past appointments" not in world.llm.requests[-1].system


async def test_appointments_export_as_a_file(app: FastAPI, client: AsyncClient) -> None:
    await app.state.services.agents.ensure_dev_agent("clinic", SEEDS / "clinic.yaml")
    res = await client.get("/api/appointments.csv")
    assert res.status_code == 200 and res.text.removeprefix("﻿").startswith("Appointment")
