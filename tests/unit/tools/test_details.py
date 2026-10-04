from typing import Any

from heydesk.ports.llm import ToolCall
from heydesk.runtime.tool import ToolResult, run_tool
from heydesk.tools import details
from tests.conftest import AGENT, make_ctx


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


async def test_a_time_outside_the_business_hours_is_refused() -> None:
    hours = {day: ["11:00", "22:00"] for day in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")}
    agent = AGENT.model_copy(update={"knowledge": {"hours": hours}})
    call = ToolCall("1", "set_details", {"task": "order", "details": {"pickup_time": "11 pm"}})

    late = await run_tool(details.TOOL, call, make_ctx(agent=agent))
    ok = await run_tool(
        details.TOOL,
        ToolCall("2", "set_details", {"task": "order", "details": {"pickup_time": "9 pm"}}),
        make_ctx(agent=agent),
    )

    assert not late.ok and "open 11:00–22:00" in late.data["problems"]["pickup_time"]
    assert ok.ok and ok.data == {"saved": {"pickup_time": "today, 9:00 pm"}}
