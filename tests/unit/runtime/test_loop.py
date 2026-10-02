import json

from heydesk.adapters.decider.fake import FakeDecider
from heydesk.adapters.llm.fake import FakeLLM
from heydesk.ports.llm import LLMUnavailable, ToolCall
from heydesk.runtime.events import Event, HandOff, Token, ToolFinished, ToolStarted, TurnEnded
from heydesk.runtime.loop import MAX_MODEL_CALLS, Runtime, TurnInput, run_turn
from heydesk.tools import details, order
from tests.conftest import AGENT, CART, FIXED_NOW, TASKS, ListCatalog, ListOrders


def make_turn(text: str = "hi", **kw: object) -> TurnInput:
    fields: dict[str, object] = {
        "system": "SYSTEM",
        "tools": [],
        "model": "fake",
        "history": [{"role": "user", "content": "earlier"}],
        "user_text": text,
        "channel": "web",
        "agent": AGENT,
        "items": ListCatalog(),
        "orders": ListOrders(),
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
