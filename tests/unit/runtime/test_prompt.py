from heydesk.runtime.config import AgentConfig
from heydesk.runtime.prompt import CHANNEL_STYLE, PLATFORM_RULES, build_system_prompt, state_note
from tests.conftest import CART, FIXED_NOW, TASKS


def agent(**kw: object) -> AgentConfig:
    fields: dict[str, object] = {
        "preset": "restaurant",
        "name": "Test assistant",
        "business_name": "Spice Garden",
        "timezone": "Asia/Dhaka",
        "greeting": "Hi!",
        "instructions": "You are the host for {business_name}. Use {braces} as you like.",
        "knowledge": {},
        "faq": "Parking: none.",
    }
    return AgentConfig.model_validate(fields | kw)


def prompt(**kw: object) -> str:
    return build_system_prompt(agent(**kw), TASKS, "MENU", "web", FIXED_NOW)


def test_platform_rules_first_and_right_now_last() -> None:
    text = prompt()

    assert text.startswith(PLATFORM_RULES)
    headings = ("# Your role", "# Tasks", "# Knowledge\nMENU", "# FAQ\nParking", "# Right now")
    positions = [text.index(h) for h in headings]
    assert positions == sorted(positions)


def test_placeholders_are_filled_and_other_braces_left_alone() -> None:
    assert "You are the host for Spice Garden. Use {braces} as you like." in prompt()


def test_the_owners_notes_come_right_after_the_role_text() -> None:
    text = prompt(notes="Be formal. Call guests {business_name} friends.")

    assert "# Notes from the business\nBe formal. Call guests Spice Garden friends." in text
    assert (
        text.index("# Your role") < text.index("# Notes from the business") < text.index("# Tasks")
    )
    assert "# Notes" not in prompt()


def test_no_faq_section_when_empty() -> None:
    assert "# FAQ" not in prompt(faq="  ")


def test_right_now_has_the_channel_the_local_time_and_the_next_two_weeks() -> None:
    section = prompt().split("# Right now\n")[1]

    assert section.splitlines() == [
        f"Channel: {CHANNEL_STYLE['web']}",
        "Now: Tuesday 29 September 2026, 14:00",  # 08:00 UTC is 14:00 in Dhaka
        (
            "Next 14 days: Wed 30 Sep, Thu 1 Oct, Fri 2 Oct, Sat 3 Oct, Sun 4 Oct, Mon 5 Oct, "
            "Tue 6 Oct, Wed 7 Oct, Thu 8 Oct, Fri 9 Oct, Sat 10 Oct, Sun 11 Oct, Mon 12 Oct, Tue 13 Oct"
        ),
    ]


def test_the_tasks_section_lists_each_task_and_its_details() -> None:
    section = prompt().split("# Tasks\n")[1].split("\n\n")[0]
    assert (
        "- order (pickup order): when the visitor wants to order food or buy something. Details: items (with "
        "update_order), pickup time, name, phone, note (optional). Finish with place_order."
    ) in section


def test_the_state_note_says_what_is_known_and_what_to_ask_next() -> None:
    state = {"cart": CART, "details": {"pickup_time": "19:30"}}
    assert state_note(TASKS, state, "BDT") == (
        "\n# Conversation state\n"
        "Task: pickup order\n"
        "Have: items = 2 × Chicken Biryani, 1 × Borhani, total ৳1,030; "
        "pickup time = today, 7:30 pm\n"
        "Still needed: name, phone\n"
        'Next: ask for the name. Suggested: "What name should I put the order under?"\n'
    )


def test_with_everything_there_the_state_note_asks_for_the_read_back() -> None:
    have = {"pickup_time": "19:30", "name": "Kayes", "phone": "01712345678"}
    note = state_note(TASKS, {"cart": CART, "details": have}, "BDT")
    assert "Still needed: nothing\n" in note  # the note is optional
    assert "Next: read all of that back and ask for a clear yes. Only after the yes, call " in note


def test_no_state_note_before_a_task_starts() -> None:
    assert state_note(TASKS, {}, "BDT") == ""
