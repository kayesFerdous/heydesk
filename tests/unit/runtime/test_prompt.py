from heydesk.runtime.config import AgentConfig
from heydesk.runtime.prompt import CHANNEL_STYLE, PLATFORM_RULES, build_system_prompt
from tests.conftest import FIXED_NOW


def agent(**kw: object) -> AgentConfig:
    fields: dict[str, object] = {
        "preset": "restaurant",
        "name": "Test assistant",
        "business_name": "Spice Garden",
        "timezone": "Asia/Dhaka",
        "greeting": "Hi!",
        "instructions": "You are the host for {business_name}. Use {braces} as you like.",
        "knowledge": {},
        "tools": [],
        "faq": "Parking: none.",
    }
    return AgentConfig.model_validate(fields | kw)


def prompt(**kw: object) -> str:
    return build_system_prompt(agent(**kw), "MENU", "web", FIXED_NOW)


def test_platform_rules_first_and_right_now_last() -> None:
    text = prompt()

    assert text.startswith(PLATFORM_RULES)
    headings = ("# Your role", "# Knowledge\nMENU", "# FAQ\nParking", "# Right now")
    positions = [text.index(h) for h in headings]
    assert positions == sorted(positions)


def test_placeholders_are_filled_and_other_braces_left_alone() -> None:
    assert "You are the host for Spice Garden. Use {braces} as you like." in prompt()


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
