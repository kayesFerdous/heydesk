"""The tools, shared by every agent (design §7). An agent gets the ones its jobs need (D32)."""

from heydesk.runtime.tasks import Task
from heydesk.runtime.tool import Tool
from heydesk.tools import details, handoff, items, order, place_order, submit_request

TOOLS: dict[str, Tool] = {
    tool.name: tool
    for tool in (
        order.TOOL,
        items.TOOL,
        details.TOOL,
        place_order.TOOL,
        submit_request.TOOL,
        handoff.TOOL,
    )
}


def tools_for(tasks: dict[str, Task], *, search: bool) -> list[Tool]:
    """The tools an agent needs for its jobs (D32): each job's finishing tool, `update_order`
    for a job that takes a cart, `set_details` for any job, `search_items` when the items
    aren't all in the prompt, and hand-off always. In `TOOLS` order."""
    names = {"handoff_to_human"}
    if tasks:
        names.add("set_details")
    for task in tasks.values():
        names.add(task.finish_with)
        if any(d.type == "cart" for d in task.details):
            names.add("update_order")
    if search:
        names.add("search_items")
    return [tool for name, tool in TOOLS.items() if name in names]
