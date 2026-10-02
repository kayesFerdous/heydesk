"""The tools, shared by every agent (design §7). An agent's config lists the ones it uses."""

from heydesk.runtime.tool import Tool
from heydesk.tools import details, handoff, items, order, place_order

TOOLS: dict[str, Tool] = {
    tool.name: tool
    for tool in (order.TOOL, items.TOOL, details.TOOL, place_order.TOOL, handoff.TOOL)
}
