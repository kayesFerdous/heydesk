from typing import Any

from heydesk.adapters.decider.fake import FakeDecider
from heydesk.ports.llm import ToolCall
from heydesk.runtime.tool import ToolResult, run_tool
from heydesk.tools import submit_request
from tests.conftest import CART, ListRequests, make_ctx

STATE = {
    "task": "message",
    "cart": CART,  # an order in progress keeps its cart and details
    "details": {
        "body": "Is there a table for 12 on Friday?",
        "name": "Kayes",
        "phone": "01712345678",
        "pickup_time": "19:30",
    },
}
YES = {"read_back": 0.95, "said_yes": 0.95}


async def submit(state: dict[str, Any], decider: FakeDecider, sent: ListRequests) -> ToolResult:
    ctx = make_ctx(state, decider=decider, requests=sent, agent_said="…?", visitor_said="yes")
    return await run_tool(submit_request.TOOL, ToolCall("1", "submit_request", {}), ctx)


async def test_a_yes_sends_the_message_and_closes_the_task() -> None:
    decider, sent = FakeDecider([YES]), ListRequests()
    result = await submit(STATE, decider, sent)

    assert result.ok
    [request] = sent.sent
    assert request.task == "message"
    assert request.details == {"body": "Is there a table for 12 on Friday?"}
    assert (request.name, request.phone) == ("Kayes", "01712345678")
    assert result.state == {"task": None, "details": {"pickup_time": "19:30"}}
    assert "name = Kayes" in decider.asked[0]["details_to_confirm"]


async def test_no_yes_sends_nothing() -> None:
    sent = ListRequests()
    result = await submit(STATE, FakeDecider(), sent)  # the fake says no

    assert not result.ok and "clear yes" in result.data["error"]
    assert sent.sent == []


async def test_a_missing_detail_is_asked_for_before_any_check() -> None:
    decider, sent = FakeDecider([YES]), ListRequests()
    state = STATE | {"details": {"body": "Call me back", "name": "Kayes"}}
    result = await submit(state, decider, sent)

    assert not result.ok and result.data["error"] == "The phone is still needed. Ask for it first."
    assert decider.asked == [] and sent.sent == []
