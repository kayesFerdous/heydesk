"""`set_details`: saves what the visitor told us, parsed by Python (rule 1)."""

from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from heydesk.runtime.schedule import WeeklyHours
from heydesk.runtime.tasks import display_detail, parse_detail
from heydesk.runtime.tool import Tool, ToolContext, ToolResult, fail


class SetDetailsInput(BaseModel):
    task: str = Field(description="The task's id from # Tasks, like 'order'")
    details: dict[str, str] = Field(
        description='Detail name → what the visitor said, like {"pickup_time": "7:30 pm"}'
    )


async def set_details(ctx: ToolContext, args: SetDetailsInput) -> ToolResult:
    task = ctx.tasks.get(args.task)
    if task is None:
        return fail(f"There's no task {args.task!r}. Use one of: {', '.join(ctx.tasks)}.")
    kinds = {d.name: d.type for d in task.details}
    now = ctx.now.astimezone(ZoneInfo(ctx.agent.timezone))
    hours = WeeklyHours.model_validate(ctx.agent.knowledge.get("hours") or {})
    details = dict(ctx.state.get("details", {}))  # a new dict: the old one stays as it was
    saved, problems = {}, {}
    for name, said in args.details.items():
        if name not in kinds:
            problems[name] = f"Not a detail of this task. Use: {', '.join(kinds)}."
            continue
        try:
            details[name] = parse_detail(kinds[name], said, now, hours)
        except ValueError as error:
            problems[name] = str(error)
            continue
        saved[name] = display_detail(kinds[name], details[name], ctx.agent.currency)

    data: dict[str, object] = {"saved": saved}
    if problems:
        data["problems"] = problems  # the good ones are saved anyway
    return ToolResult(ok=not problems, data=data, state={"task": args.task, "details": details})


TOOL = Tool(
    name="set_details",
    description=(
        "Save details the visitor gave for a task (a time, a name, a phone number…), "
        "as soon as they give them. Pass what they said; Python checks it."
    ),
    input_model=SetDetailsInput,
    run=set_details,
    label="Noting that down…",
)
