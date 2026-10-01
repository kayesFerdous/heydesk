"""`search_items`: finds items when the list is too long for the prompt (D17)."""

from pydantic import BaseModel, Field

from heydesk.runtime.items import item_line
from heydesk.runtime.tool import Tool, ToolContext, ToolResult


class SearchItemsInput(BaseModel):
    query: str = Field(
        min_length=1, max_length=100, description="A few words: a name, an ingredient, a category"
    )


async def search_items(ctx: ToolContext, args: SearchItemsInput) -> ToolResult:
    found = await ctx.items.search(args.query)
    if not found:
        return ToolResult(ok=True, data={"items": [], "hint": "Nothing matched. Try other words."})
    return ToolResult(ok=True, data={"items": [item_line(i, ctx.agent.currency) for i in found]})


TOOL = Tool(
    name="search_items",
    description=(
        "Find items by a few words (a name, an ingredient, a category, a tag like 'vegetarian'). "
        "Returns up to 10 matches, each with its code and price."
    ),
    input_model=SearchItemsInput,
    run=search_items,
    label="Looking that up…",
)
