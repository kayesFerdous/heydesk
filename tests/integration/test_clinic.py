"""The clinic preset: doctors as items, and an appointment request saved only after a yes."""

from sqlalchemy import select

from heydesk.models import Record
from heydesk.ports.llm import ToolCall
from heydesk.runtime.items import ItemQuery, find_items
from heydesk.services.agents import LiveAgent
from tests.conftest import SEED, World

DETAILS = {
    "doctor": "Dr. Nasrin Akter",
    "day_time": "Saturday evening",
    "name": "Kayes",
    "phone": "01712345678",
}
READ_BACK = "An appointment request with Dr. Akter on Saturday evening for Kayes. Shall I send it?"


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
        "{days: sat, sun, mon, hours: 5 pm to 9 pm, languages: english, bangla, years: 12}"
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
        ("I need a heart doctor on Saturday evening, I'm Kayes, 01712345678", READ_BACK),
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
    assert record.details == {"doctor": "Dr. Nasrin Akter", "day time": "Saturday evening"}
