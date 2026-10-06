"""The tools (design §7). An agent gets what its jobs need, its preset's own tools, and the shared ones (D37)."""

from collections.abc import Sequence

from heydesk.runtime.tasks import Task
from heydesk.runtime.tool import Tool
from heydesk.tools import (
    clinic,
    details,
    handoff,
    items,
    knowledge,
    order,
    place_order,
    submit_request,
)

TOOLS: dict[str, Tool] = {
    tool.name: tool
    for tool in (
        order.TOOL,
        items.TOOL,
        clinic.FIND_DOCTOR,
        clinic.CANCEL_APPOINTMENT,
        clinic.RESCHEDULE_APPOINTMENT,
        knowledge.TOOL,
        details.TOOL,
        place_order.TOOL,
        submit_request.TOOL,
        handoff.TOOL,
    )
}


def tools_for(
    tasks: dict[str, Task], own: Sequence[str] = (), *, search: bool, documents: bool = False
) -> list[Tool]:
    """An agent's tools (D37): each job's finishing tool, `update_order` for a job that takes
    a cart, `set_details` for any job, the preset's `own` tools, and the shared ones:
    `search_items` when the items aren't all in the prompt, `search_knowledge` when there are
    documents (D35), and hand-off always. In `TOOLS` order."""
    names = {"handoff_to_human", *own}
    if tasks:
        names.add("set_details")
    for task in tasks.values():
        names.add(task.finish_with)
        if any(d.type == "cart" for d in task.details):
            names.add("update_order")
    if search:
        names.add("search_items")
    if documents:
        names.add("search_knowledge")
    return [tool for name, tool in TOOLS.items() if name in names]
