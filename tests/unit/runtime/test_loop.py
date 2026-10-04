import json
from collections.abc import AsyncGenerator

from heydesk.adapters.decider.fake import FakeDecider
from heydesk.adapters.llm.fake import FakeLLM
from heydesk.ports.llm import (
    ChatRequest,
    Finished,
    LLMUnavailable,
    ModelEvent,
    TextDelta,
    ToolCall,
    Usage,
)
from heydesk.runtime.events import Event, HandOff, Token, ToolFinished, ToolStarted, TurnEnded
from heydesk.runtime.loop import MAX_MODEL_CALLS, Runtime, TurnInput, run_turn
from heydesk.tools import details, handoff, order
from tests.conftest import AGENT, CART, FIXED_NOW, TASKS, ListCatalog, ListOrders, ListRequests


def make_turn(text: str = "hi", **kw: object) -> TurnInput:
    fields: dict[str, object] = {
        "system": "SYSTEM",
        "model": "fake",
        "history": [{"role": "user", "content": "earlier"}],
        "user_text": text,
        "channel": "web",
        "agent": AGENT,
        "items": ListCatalog(),
        "orders": ListOrders(),
        "requests": ListRequests(),
        "tools": [],
        "tasks": TASKS,
        "now": FIXED_NOW,
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


ORDER = ToolCall(
    "call_1",
    "update_order",
    {"lines": [{"code": "chk-biryani", "quantity": 2}, {"code": "borhani", "quantity": 1}]},
)


async def test_a_tool_runs_and_its_result_goes_back_to_the_model() -> None:
    llm = FakeLLM([ORDER, "That's ৳1,030 for the two of them."])
    events = await collect(make_turn("two biryanis and a borhani", tools=[order.TOOL]), llm)

    assert ToolStarted("update_order", "Updating your order…") in events
    assert ToolFinished("update_order", ok=True) in events
    end = events[-1]
    assert isinstance(end, TurnEnded) and not end.handed_off
    assert [m["role"] for m in end.added] == ["user", "assistant", "tool", "assistant"]
    result = json.loads(end.added[2]["content"])
    assert result["total"] == "৳1,030"
    assert end.state["cart"]["total"] == 103000  # paisa
    # The model saw the tool's spec, then its result in the second call
    assert [t.name for t in llm.requests[0].tools] == ["update_order"]
    assert llm.requests[1].messages[-1] == end.added[2]
    assert [s["name"] for s in end.trace["steps"]] == ["model", "tool", "model"]


async def test_a_turn_that_never_stops_calling_tools_hands_off() -> None:
    llm = FakeLLM([ORDER] * MAX_MODEL_CALLS)
    end = (await collect(make_turn(tools=[order.TOOL]), llm))[-1]

    assert isinstance(end, TurnEnded) and end.handed_off
    assert len(llm.requests) == MAX_MODEL_CALLS
    assert end.added[-2]["role"] == "tool"  # every call got its result
    assert end.added[-1] == {"role": "assistant", "content": "Please call us."}


async def test_asking_for_a_person_hands_off_after_the_reply() -> None:
    llm = FakeLLM([ToolCall("call_1", "handoff_to_human", {}), "Of course. Please call us."])
    events = await collect(make_turn("can I talk to a person?", tools=[handoff.TOOL]), llm)

    *_, card, end = events
    assert card == HandOff("Please call us.", "+880 1700 000000", None)
    assert isinstance(end, TurnEnded) and end.handed_off
    # The model's own reply is the last word: no closing line added, unlike a failure
    assert end.added[-1] == {"role": "assistant", "content": "Of course. Please call us."}
    assert json.loads(end.added[-2]["content"])["phone"] == "+880 1700 000000"


async def test_the_state_note_is_rebuilt_before_every_model_call() -> None:
    said = {"pickup_time": "7:30", "name": "Kayes"}
    call = ToolCall("call_2", "set_details", {"task": "order", "details": said})
    llm = FakeLLM([call, "And the best number to reach you?"])
    turn = make_turn("for 7:30, I'm Kayes", tools=[details.TOOL], state={"cart": CART})
    end = (await collect(turn, llm))[-1]

    first, second = (request.system for request in llm.requests)
    assert first.startswith("SYSTEM\n# Conversation state\nTask: pickup order\n")
    assert "Next: ask for the pickup time." in first
    assert "pickup time = today, 7:30 pm; name = Kayes\n" in second
    assert "Still needed: phone\nNext: ask for the phone." in second
    assert isinstance(end, TurnEnded)
    assert end.state["details"] == {"pickup_time": "19:30", "name": "Kayes"}


class EndsWith(FakeLLM):
    """A model whose only step ends with `finished`: a refusal, or None for a lost stream."""

    def __init__(self, finished: Finished | None) -> None:
        super().__init__()
        self.finished = finished

    async def stream(self, request: ChatRequest) -> AsyncGenerator[ModelEvent]:
        self.requests.append(request)
        yield TextDelta("Sorry")
        if self.finished:
            yield self.finished


async def test_a_refusal_or_a_lost_stream_is_a_failure() -> None:
    refusal = Finished("refusal", {"role": "assistant", "content": "Sorry"}, Usage())
    for finished in (refusal, None):
        end = (await collect(make_turn(), EndsWith(finished)))[-1]
        assert isinstance(end, TurnEnded) and end.handed_off
        # What was streamed stays, then the polite line
        assert end.added[-2:] == [
            {"role": "assistant", "content": "Sorry"},
            {"role": "assistant", "content": "Please call us."},
        ]


async def test_a_provider_failure_after_a_tool_keeps_the_transcript_valid() -> None:
    llm = FakeLLM([ORDER, LLMUnavailable("timeout")])
    end = (await collect(make_turn(tools=[order.TOOL]), llm))[-1]

    assert isinstance(end, TurnEnded) and end.handed_off
    assert [m["role"] for m in end.added] == ["user", "assistant", "tool", "assistant"]
    assert end.added[-1]["content"] == "Please call us."
    assert end.state["cart"]["total"] == 103000  # what the tool did is kept


async def test_a_barge_in_during_a_tool_stops_before_the_next_model_call() -> None:
    turn = make_turn(tools=[order.TOOL])
    llm = FakeLLM([ORDER, "never said"])
    events: list[Event] = []
    async for event in run_turn(turn, Runtime(llm, FakeDecider())):
        events.append(event)
        if isinstance(event, ToolStarted):
            turn.interrupt.set()

    end = events[-1]
    assert isinstance(end, TurnEnded) and end.interrupted and not end.handed_off
    assert len(llm.requests) == 1
    assert end.added[-1]["role"] == "tool"  # the call still got its result


async def test_an_unknown_tool_gets_a_hint_and_the_model_carries_on() -> None:
    llm = FakeLLM([ToolCall("call_1", "fly_to_moon", {}), "I can't do that."])
    events = await collect(make_turn(), llm)

    assert ToolStarted("fly_to_moon", "") in events
    assert ToolFinished("fly_to_moon", ok=False) in events
    end = events[-1]
    assert isinstance(end, TurnEnded) and not end.handed_off
    assert "no tool called 'fly_to_moon'" in end.added[2]["content"]
