from typing import Any

from heydesk.ports.llm import ToolCall
from heydesk.runtime.tool import ToolResult, run_tool
from heydesk.tools import details
from tests.conftest import make_ctx


async def save(state: dict[str, Any], task: str = "order", **said: str) -> ToolResult:
    ctx = make_ctx(state)
    call = ToolCall("1", "set_details", {"task": task, "details": said})
    return await run_tool(details.TOOL, call, ctx)


async def test_details_are_parsed_and_added_to_the_ones_saved_before() -> None:
    result = await save({"details": {"phone": "01712345678"}}, pickup_time="7:30", name="Kayes")

    assert result.ok
    assert result.data == {"saved": {"pickup_time": "today, 7:30 pm", "name": "Kayes"}}
    assert result.state == {
        "task": "order",
        "details": {"phone": "01712345678", "pickup_time": "19:30", "name": "Kayes"},
    }


async def test_good_details_are_saved_and_bad_ones_explained() -> None:
    result = await save({}, name="Kayes", pickup_time="11 am", table="4")

    assert not result.ok
    assert result.state == {"task": "order", "details": {"name": "Kayes"}}
    problems = result.data["problems"]
    assert set(problems) == {"pickup_time", "table"}
    assert "it's 2:00 pm now" in problems["pickup_time"]
    assert "Not a detail of this task" in problems["table"]


async def test_an_unknown_task_is_refused() -> None:
    result = await save({}, task="booking", name="Kayes")
    assert not result.ok and "Use one of: order, message" in result.data["error"]
    assert result.state is None
