from heydesk.runtime.config import AgentConfig
from heydesk.runtime.prompt import (
    BANGLA_VOICE,
    CHANNEL_STYLE,
    PLATFORM_RULES,
    build_system_prompt,
)
from tests.conftest import FIXED_NOW

SHOP_SECTION = "# Your role\nYou are the host for {business_name}. Use {braces} as you like."


def agent(**kw: object) -> AgentConfig:
    fields: dict[str, object] = {
        "name": "Test assistant",
        "business_name": "Spice Garden",
        "timezone": "Asia/Dhaka",
        "greeting": "Hi!",
        "faq": "Parking: none.",
    }
    return AgentConfig.model_validate(fields | kw)


def prompt(documents: list[str] | None = None, **kw: object) -> str:
    return build_system_prompt(agent(**kw), SHOP_SECTION, "web", FIXED_NOW, documents or [])


def test_platform_rules_first_and_right_now_last() -> None:
    text = prompt()

    assert text.startswith(PLATFORM_RULES)
    assert "Codes in [brackets] (a product's) are for tools only" in text
    text = prompt(notes="Be formal.", documents=["Menu guide"])
    headings = ("# Your role", "# Notes", "# FAQ\nParking", "# Documents", "# Right now")
    positions = [text.index(h) for h in headings]
    assert positions == sorted(positions)


def test_placeholders_are_filled_and_other_braces_left_alone() -> None:
    assert "You are the host for Spice Garden. Use {braces} as you like." in prompt()


def test_the_owners_notes_come_right_after_the_role_text() -> None:
    text = prompt(notes="Be formal. Call guests {business_name} friends.")

    assert "# Notes from the business\nBe formal. Call guests Spice Garden friends." in text
    assert text.index("# Your role") < text.index("# Notes from the business") < text.index("# FAQ")
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


def test_right_now_says_if_we_are_open_today_and_marks_the_closed_days() -> None:
    hours = {"mon": None, "tue": ["11:00", "22:00"], "wed": None}  # Thu–Sun left out: closed
    section = prompt(hours=hours).split("# Right now\n")[1]

    assert "Today: open 11:00–22:00" in section
    assert "Wed 30 Sep (closed), Thu 1 Oct (closed)" in section
    assert "Tue 6 Oct, Wed 7 Oct (closed)" in section

    closed = prompt(hours={"tue": None}).split("# Right now\n")[1]
    assert "Today: closed" in closed


def test_documents_are_named_with_when_to_search_them() -> None:
    assert "# Documents" not in prompt()
    assert "The business has added documents: Menu guide, Allergens." in prompt(
        documents=["Menu guide", "Allergens"]
    )


def test_on_voice_a_misheard_name_is_matched_to_a_listed_one_and_checked() -> None:
    text = build_system_prompt(agent(), SHOP_SECTION, "voice", FIXED_NOW, [])
    assert "can be misheard" in text and "take the closest listed one and check it" in text


def test_a_call_in_bangla_is_told_to_write_everything_in_bangla_letters() -> None:
    def right_now(channel: str, language: str) -> str:
        text = build_system_prompt(agent(), SHOP_SECTION, channel, FIXED_NOW, [], language)
        return text.split("# Right now\n")[1]

    assert BANGLA_VOICE in right_now("voice", "bn")
    assert BANGLA_VOICE not in right_now("web", "bn")  # the chat shows Latin letters fine
    assert BANGLA_VOICE not in right_now("voice", "en")
