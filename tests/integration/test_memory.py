"""What the agent remembers about a visitor (D124): a note they gave on their own, kept with
`remember` and in the prompt of their next conversations from the same chat or browser."""

from heydesk.ports.llm import ToolCall
from tests.conftest import World

SECTION = "# What you remember about them"


def note(about: str, text: str) -> ToolCall:
    return ToolCall("r", "remember", {"about": about, "note": text})


async def test_a_note_is_in_the_next_conversations_prompt(world: World) -> None:
    agent = await world.agent("shop")
    talk = world.services.conversations
    world.llm.script = [note("Colours", "likes dark colours"), "Dark ta valo hobe!"]
    await talk.reply(agent, "telegram", "chat-1", "ami dark colour pochondo kori")
    assert SECTION not in world.llm.requests[0].system

    world.clock.advance(hours=13)  # the same chat, started afresh (D157)
    world.llm.script = ["Welcome back!"]
    await talk.reply(agent, "telegram", "chat-1", "hi")
    assert "- colours: likes dark colours" in world.llm.requests[-1].system

    world.llm.script = ["Hi!"]
    await talk.reply(agent, "telegram", "chat-2", "hi")  # someone else: nothing
    assert SECTION not in world.llm.requests[-1].system


async def test_a_note_on_the_same_thing_replaces_the_old_one(world: World) -> None:
    agent = await world.agent("restaurant")
    talk = world.services.conversations
    world.llm.script = [note("diet", "eats no beef"), note("spice", "likes it mild"), "Noted."]
    await talk.reply(agent, "messenger", "psid-1", "no beef, and not too spicy")
    world.llm.script = [note("Spice", "likes it very spicy now"), "Sure!"]
    await talk.reply(agent, "messenger", "psid-1", "actually make it spicy")
    world.llm.script = ["Here's the menu."]
    await talk.reply(agent, "messenger", "psid-1", "menu?")  # the prompt is built each turn

    system = world.llm.requests[-1].system
    assert "- diet: eats no beef\n- spice: likes it very spicy now" in system
    assert "likes it mild" not in system


async def test_the_web_chat_and_a_call_from_one_browser_share_the_notes(world: World) -> None:
    agent = await world.agent("clinic")
    talk = world.services.conversations

    async def say(channel: str, script: list[object]) -> None:
        world.llm.script = script
        turn = await talk.start_turn(agent, None, "…", channel, "web:browser-1", "browser-1")
        [_ async for _ in turn.events]

    await say("web", [note("allergy", "allergic to penicillin"), "Thanks for telling me."])
    await say("voice", ["Hello again."])
    assert "- allergy: allergic to penicillin" in world.llm.requests[-1].system
