"""The clinic preset: doctors as items, and an appointment request saved only after a yes."""

from sqlalchemy import select

from heydesk.models import Conversation, Record
from heydesk.ports.llm import ToolCall
from heydesk.runtime.items import ItemQuery, find_items
from heydesk.services.agents import LiveAgent
from tests.conftest import SEED, World

DETAILS = {
    "doctor": "Dr. Hasan",  # Tue and Wed, 18:00-21:00; tomorrow is Wed 30 Sep
    "appointment_time": "tomorrow at 7 pm",
    "name": "Kayes",
    "phone": "01712345678",
}
READ_BACK = (
    "An appointment request with Dr. Hasan, Wed 30 Sep at 7:00 pm, for Kayes. Shall I send it?"
)


async def clinic(world: World) -> LiveAgent:
    return await world.services.agents.ensure_dev_agent("clinic", SEED.parent / "clinic.yaml")


async def test_the_doctors_are_in_the_prompt_with_their_own_facts(world: World) -> None:
    agent = await clinic(world)

    assert agent.greeting.startswith("Hello, thanks for contacting Lakeview Clinic.")
    assert set(agent.tasks) == {"appointment", "message"}
    assert "Emergency number: 999" in agent.knowledge
    assert (
        "Doctors. Each line: name [code] price (tags): description {extra facts}" in agent.knowledge
    )
    assert (
        "- Dr. Nasrin Akter [dr-akter] ৳800 (female): Heart and blood pressure. Sees adults. "
        "{days: sat, sun, mon, hours: 17:00-21:00, languages: english, bangla, years: 12}"
    ) in agent.knowledge


async def test_a_heart_doctor_on_saturday_evening_is_found_by_the_data(world: World) -> None:
    agent = await clinic(world)
    doctors = await world.services.items.all(agent.workspace_id, agent.agent_id)

    query = ItemQuery.model_validate(
        {
            "category": "cardio",
            "filters": [{"field": "days", "op": "includes", "value": "sat"}],
        }
    )

    assert [d.code for d in find_items(doctors, query)] == ["dr-akter"]  # not Dr. Hasan: Tue, Wed
    cheap = ItemQuery.model_validate({"max_price": 450, "tags": ["female"]})
    assert [d.code for d in find_items(doctors, cheap)] == ["dr-noor"]


async def test_an_appointment_request_is_saved_only_after_a_yes(world: World) -> None:
    agent = await clinic(world)
    world.decider.script = [
        {"read_back": 0.95, "said_yes": 0.3},
        {"read_back": 0.95, "said_yes": 0.95},
    ]
    conversations = world.services.conversations
    conv_id: str | None = None
    for text, reply in (
        ("Dr. Hasan tomorrow at 7 pm please, I'm Kayes, 01712345678", READ_BACK),
        ("hmm, maybe", READ_BACK),
        ("yes please", "Sent! The clinic will call you to confirm."),
    ):
        calls = {
            0: [ToolCall("c1", "set_details", {"task": "appointment", "details": DETAILS})],
            1: [ToolCall("c2", "submit_request", {})],
            2: [ToolCall("c3", "submit_request", {})],
        }[0 if conv_id is None else (1 if "maybe" in text else 2)]
        world.llm.script = [*calls, reply]
        turn = await conversations.start_turn(agent, conv_id, text, "web")
        [_ async for _ in turn.events]
        conv_id = turn.conversation_id
        async with world.sessions() as s:
            saved = list(await s.scalars(select(Record)))
        assert len(saved) == (1 if text == "yes please" else 0)

    [record] = saved
    assert (record.kind, record.job, record.customer_name) == ("request", "appointment", "Kayes")
    assert record.details == {
        "doctor": "Dr. Imran Hasan",
        "appointment time": "Wed 30 Sep, 7:00 pm",
    }


async def test_the_clinic_cant_be_booked_after_it_closes(world: World) -> None:
    agent = await clinic(world)
    world.llm.script = [
        ToolCall(
            "c1",
            "set_details",
            {"task": "appointment", "details": DETAILS | {"appointment_time": "tomorrow at 11 pm"}},
        ),
        "Sorry, we close at 9 pm on Wednesdays. Is there an earlier time?",
    ]

    turn = await world.services.conversations.start_turn(agent, None, "tomorrow at 11 pm", "web")
    [_ async for _ in turn.events]

    async with world.sessions() as s:
        conv = await s.get(Conversation, turn.conversation_id)
    assert conv is not None
    assert "appointment_time" not in conv.state["details"]  # not saved: the hours said no
    assert "Wednesdays we're open 09:00–21:00" in conv.messages[-2]["content"]


async def test_a_doctor_is_booked_only_when_they_see_patients(world: World) -> None:
    agent = await clinic(world)

    async def details(said: dict[str, str]) -> dict[str, object]:
        world.llm.script = [
            ToolCall("c1", "set_details", {"task": "appointment", "details": said}),
            "Let me suggest another time.",
        ]
        turn = await world.services.conversations.start_turn(agent, None, "book it", "web")
        [_ async for _ in turn.events]
        async with world.sessions() as s:
            conv = await s.get(Conversation, turn.conversation_id)
        assert conv is not None
        return {"state": conv.state["details"], "tool": conv.messages[-2]["content"]}

    akter_on_wednesday = await details({"doctor": "Akter", "appointment_time": "tomorrow at 6 pm"})
    assert akter_on_wednesday["state"] == {"doctor": "Dr. Nasrin Akter"}  # the doctor is kept
    assert "sees patients on sat, sun, mon, not on Wednesdays" in str(akter_on_wednesday["tool"])

    hasan_too_early = await details({"doctor": "dr-hasan", "appointment_time": "tomorrow at 5 pm"})
    assert "from 6:00 pm to 9:00 pm" in str(hasan_too_early["tool"])

    nobody = await details({"doctor": "Dr. Who"})
    assert nobody["state"] == {} and "isn't one of our doctors" in str(nobody["tool"])


async def test_find_doctor_works_out_the_day_in_python(world: World) -> None:
    agent = await clinic(world)
    world.llm.script = [
        ToolCall("c1", "find_doctor", {"speciality": "Cardiology", "day": "tomorrow"}),
        "Tomorrow, Dr. Hasan sees heart patients from 6 to 9 pm.",
    ]

    turn = await world.services.conversations.start_turn(
        agent, None, "heart doctor tomorrow?", "web"
    )
    [_ async for _ in turn.events]

    async with world.sessions() as s:
        conv = await s.get(Conversation, turn.conversation_id)
    assert conv is not None
    found = conv.messages[-2]["content"]
    assert "Wednesday 30 Sep" in found and "Dr. Imran Hasan" in found
    assert "Dr. Nasrin Akter" not in found  # she doesn't work on Wednesdays


async def say(world: World, agent: LiveAgent, conv: str | None, script: list[object]) -> str:
    """One turn with a scripted model; returns the conversation id."""
    world.llm.script = script
    turn = await world.services.conversations.start_turn(agent, conv, "…", "web")
    [_ async for _ in turn.events]
    return turn.conversation_id


async def book(world: World, agent: LiveAgent) -> Record:
    world.decider.script = [{"read_back": 0.95, "said_yes": 0.95}]
    details = ToolCall("c1", "set_details", {"task": "appointment", "details": DETAILS})
    conv = await say(world, agent, None, [details, READ_BACK])
    await say(world, agent, conv, [ToolCall("c2", "submit_request", {}), "Sent."])
    async with world.sessions() as s:
        return (await s.scalars(select(Record))).one()


async def tool_said(world: World, conv: str) -> str:
    async with world.sessions() as s:
        c = await s.get(Conversation, conv)
    assert c is not None
    return str(c.messages[-2]["content"])


async def test_an_appointment_is_cancelled_with_its_code_and_name_after_a_yes(
    world: World,
) -> None:
    agent = await clinic(world)
    record = await book(world, agent)
    assert record.code == "A-101"

    def cancel(code: str, name: str) -> list[object]:
        return [ToolCall("c", "cancel_appointment", {"code": code, "name": name}), "…"]

    world.decider.script = [
        {"read_back": 0.1, "said_yes": 0.1},
        {"read_back": 0.95, "said_yes": 0.95},
    ]
    wrong = await say(world, agent, None, cancel("a101", "Someone Else"))
    assert "No appointment matches that code and name" in await tool_said(world, wrong)
    assert "Hasan" not in await tool_said(world, wrong)  # nothing about it leaks

    conv = await say(world, agent, None, cancel("a 101", "kayes"))  # no read-back yet
    assert "Cancel appointment A-101 with Dr. Imran Hasan on Wed 30 Sep, 7:00 pm" in (
        await tool_said(world, conv)
    )
    async with world.sessions() as s:
        assert (await s.get(Record, record.id)).status == "new"  # type: ignore[union-attr]

    await say(world, agent, conv, cancel("A-101", "Kayes"))  # after the yes
    async with world.sessions() as s:
        assert (await s.get(Record, record.id)).status == "cancelled"  # type: ignore[union-attr]


async def test_an_appointment_moves_only_to_a_time_the_doctor_works(world: World) -> None:
    agent = await clinic(world)
    record = await book(world, agent)

    def move(when: str) -> list[object]:
        args = {"code": "A-101", "name": "Kayes", "new_time": when}
        return [ToolCall("c", "reschedule_appointment", args), "…"]

    conv = await say(world, agent, None, move("monday at 7 pm"))  # Dr. Hasan: Tue and Wed
    assert "not on Mondays" in await tool_said(world, conv)

    world.decider.script = [{"read_back": 0.95, "said_yes": 0.95}]
    await say(world, agent, conv, move("tuesday at 8 pm"))
    async with world.sessions() as s:
        moved = await s.get(Record, record.id)
    assert moved is not None and moved.details["appointment time"] == "Tue 29 Sep, 8:00 pm"  # today
