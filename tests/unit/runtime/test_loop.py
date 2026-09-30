from heydesk.adapters.decider.fake import FakeDecider
from heydesk.adapters.llm.fake import FakeLLM
from heydesk.ports.llm import LLMUnavailable, ModelSettings
from heydesk.runtime.events import Event, HandOff, Token, TurnEnded
from heydesk.runtime.loop import Runtime, TurnInput, run_turn

HANDOFF = HandOff("Please call us.", phone="+880 1700 000000")


def make_turn(text: str = "hi", **kw: object) -> TurnInput:
    return TurnInput(
        system="SYSTEM",
        tools=[],
        settings=ModelSettings("fake"),
        history=[{"role": "user", "content": "earlier"}],
        user_text=text,
        channel="web",
        handoff=HANDOFF,
        **kw,  # type: ignore[arg-type]
    )


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


async def test_provider_failure_hands_off_politely() -> None:
    events = await collect(make_turn(), FakeLLM([LLMUnavailable("503")]))

    assert events[0] == HANDOFF
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
