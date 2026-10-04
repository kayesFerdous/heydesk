"""`search_items`: finds items by words and conditions when the list is too long for the prompt (D17, D28)."""

from heydesk.runtime.items import FIND_LIMIT, ItemQuery, find_items, item_line
from heydesk.runtime.tool import Tool, ToolContext, ToolResult


async def search_items(ctx: ToolContext, args: ItemQuery) -> ToolResult:
    found = find_items(await ctx.items.all(), args)
    if not found:
        return ToolResult(
            ok=True,
            data={"items": [], "hint": "Nothing matched. Try fewer conditions or other words."},
        )
    data: dict[str, object] = {
        "total": len(found),
        "items": [item_line(i, ctx.agent.currency) for i in found[:FIND_LIMIT]],
    }
    if len(found) > FIND_LIMIT:
        data["hint"] = f"{len(found)} matched; these are the best {FIND_LIMIT}. Add a condition."
    return ToolResult(ok=True, data=data)


TOOL = Tool(
    name="search_items",
    description=(
        "Find items. Give a few words in `query`, and/or conditions: `category`, `tags`, "
        "`min_price` / `max_price` (whole currency units), `filters` on extra facts "
        "(calories at_most 400, days includes sat). Returns the best 10 with codes and prices."
    ),
    input_model=ItemQuery,
    run=search_items,
    label="Looking that up…",
)
