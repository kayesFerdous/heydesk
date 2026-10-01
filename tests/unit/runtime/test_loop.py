from heydesk.adapters.decider.fake import FakeDecider
from heydesk.adapters.llm.fake import FakeLLM
from heydesk.ports.llm import LLMUnavailable
from heydesk.runtime.config import AgentConfig, Handoff
from heydesk.runtime.events import Event, HandOff, Token, TurnEnded
from heydesk.runtime.loop import Runtime, TurnInput, run_turn

AGENT = AgentConfig(
    preset="restaurant",
    name="Test assistant",
    business_name="Spice Garden",
    timezone="Asia/Dhaka",
    greeting="Hi, welcome to {business_name}!",
    instructions="Be brief.",
    knowledge={},
    tools=[],
    handoff=Handoff(message="Please call us.", phone="+880 1700 000000"),
)


def make_turn(text: str = "hi", **kw: object) -> TurnInput:
    fields: dict[str, object] = {
        "system": "SYSTEM",
        "tools": [],
        "model": "fake",
        "history": [{"role": "user", "content": "earlier"}],
        "user_text": text,
        "channel": "web",
        "agent": AGENT,
    }
    return TurnInput(**(fields | kw))  # type: ignore[arg-type]


async def collect(turn: TurnInput, llm: FakeLLM) -> list[Event]:
    return [e async for e in run_turn(turn, Runtime(llm, FakeDecider()))]


async def test_streams_the_reply_and_adds_it_to_the_transcript() -> None:
    llm = FakeLLM(["Hello there, friend."])
    events = await collect(make_turn("hi"), llm)

    assert "".join(e.text for e in events if isinstance(e, Token)) == "Hello there, friend."
    end = events[-1]
    assert isinstance(end, TurnEnded) and not end.handed_off and not end.interrupted
    assert end.added == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "Hello there, friend."},
    ]
    request = llm.requests[0]
    assert request.system == "SYSTEM"
    assert request.messages == [{"role": "user", "content": "earlier"}, *end.added[:1]]
    model = end.trace["steps"][0]
    assert model["stop"] == "end" and "ttft_ms" in model and model["tokens"]["output"] == 3
    assert end.trace["first_output_ms"] is not None


async def test_the_first_turn_starts_with_the_greeting_the_visitor_saw() -> None:
    llm = FakeLLM(["What can I get you?"])
    end = (await collect(make_turn("hi", history=[], is_first=True), llm))[-1]

    greeting = {"role": "assistant", "content": "Hi, welcome to Spice Garden!"}  # D8
    user = {"role": "user", "content": "hi"}
    assert isinstance(end, TurnEnded) and end.added[:2] == [greeting, user]
    assert llm.requests[0].messages == [greeting, user]


async def test_provider_failure_hands_off_politely() -> None:
    events = await collect(make_turn(), FakeLLM([LLMUnavailable("503")]))

    assert events[0] == HandOff("Please call us.", phone="+880 1700 000000")
    end = events[-1]
    assert isinstance(end, TurnEnded) and end.handed_off
    assert end.added[-1] == {"role": "assistant", "content": "Please call us."}
    assert end.trace["error"] == "503"


async def test_an_empty_reply_is_a_failure() -> None:
    end = (await collect(make_turn(), FakeLLM([""])))[-1]
    assert isinstance(end, TurnEnded) and end.handed_off
    assert end.added == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "Please call us."},
    ]


async def test_interrupt_stops_talking_and_keeps_what_was_streamed() -> None:
    turn = make_turn()
    events: list[Event] = []
    async for event in run_turn(turn, Runtime(FakeLLM(["one two three"]), FakeDecider())):
        events.append(event)
        if isinstance(event, Token):
            turn.interrupt.set()  # barge-in after the first word

    assert [e.text for e in events if isinstance(e, Token)] == ["one "]
    end = events[-1]
    assert isinstance(end, TurnEnded) and end.interrupted and not end.handed_off
    assert end.added[-1] == {"role": "assistant", "content": "one "}
