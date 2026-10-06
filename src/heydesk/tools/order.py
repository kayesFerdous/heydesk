"""`update_order`: the visitor's whole order in, the total out. Python adds it up (rule 1)."""

from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from heydesk.runtime.schedule import WeeklyHours
from heydesk.runtime.speech import money
from heydesk.runtime.tool import Tool, ToolContext, ToolResult, fail


class OrderLine(BaseModel):
    code: str = Field(description="The item's code, shown in [brackets]")
    quantity: int = Field(ge=1, le=50)


class UpdateOrderInput(BaseModel):
    lines: list[OrderLine] = Field(
        description="Every item in the order, not just the change. An empty list clears it."
    )


async def update_order(ctx: ToolContext, args: UpdateOrderInput) -> ToolResult:
    hours = WeeklyHours.model_validate(ctx.agent.knowledge.get("hours") or {})
    today = ctx.now.astimezone(ZoneInfo(ctx.agent.timezone)).date()
    if hours.is_set() and hours.on(today) is None:  # pickup is today only, so no order
        return fail(
            f"We're closed today. Our hours: {hours.render()}. Tell the visitor; take no order."
        )
    wanted: dict[str, int] = {}  # code → quantity; the same item twice is added up
    for line in args.lines:
        wanted[line.code] = wanted.get(line.code, 0) + line.quantity

    found = await ctx.items.get(list(wanted))
    unknown = [code for code in wanted if code not in found]
    if unknown:
        return fail(f"Not on the list: {', '.join(unknown)}. Use the codes in [brackets].")
    sold_out = [found[code].name for code in wanted if not found[code].available]
    if sold_out:
        return fail(f"Not available right now: {', '.join(sold_out)}. Tell the visitor.")

    currency = ctx.agent.currency
    lines, said = [], []  # for the state, and for the model
    for code, qty in wanted.items():
        item = found[code]
        lines.append({"code": code, "name": item.name, "quantity": qty, "price": item.price})
        said.append(f"{qty} × {item.name} = {money(item.price * qty, currency)}")
    total = sum(found[code].price * qty for code, qty in wanted.items())

    data = {"order": said, "total": money(total, currency)}
    return ToolResult(ok=True, data=data, state={"cart": {"lines": lines, "total": total}})


TOOL = Tool(
    name="update_order",
    description=(
        "Set the visitor's whole order: every item and its quantity. Call it after every change. "
        "Returns each line and the total; tell the visitor the total exactly as given."
    ),
    input_model=UpdateOrderInput,
    run=update_order,
    label="Updating your order…",
)
