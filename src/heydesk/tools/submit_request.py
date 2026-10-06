"""`submit_request`: saves a message for the team, only after a read-back and a yes (rule 2)."""

from pydantic import BaseModel

from heydesk.domain.values import NewRequest
from heydesk.runtime.guards import confirm
from heydesk.runtime.tasks import current_task, describe, display_detail, filled_details, next_step
from heydesk.runtime.tool import Tool, ToolContext, ToolResult, fail


class SubmitRequestInput(BaseModel):
    """No arguments: the details are read from the conversation state."""


async def submit_request(ctx: ToolContext, args: SubmitRequestInput) -> ToolResult:
    task_id = current_task(ctx.tasks, ctx.state)
    task = ctx.tasks[task_id] if task_id else None
    if task_id is None or task is None or task.finish_with != "submit_request":
        return fail("There's no message yet. Save what the visitor said with set_details first.")
    have = filled_details(task, ctx.state)
    missing = next_step(task, have)
    if missing:
        return fail(f"The {missing.label} is still needed. Ask for it first.")

    by_type = {d.type: d.name for d in task.details}
    name, phone = by_type.get("person_name"), by_type.get("phone")
    request = NewRequest(
        task=task_id,
        details={
            d.label: display_detail(d.type, have[d.name], ctx.agent.currency)
            for d in task.details
            if d.name in have and d.name not in (name, phone)
        },
        name=have.get(name or "", ""),
        phone=have.get(phone or "", ""),
    )
    details = "; ".join(describe(task, have, ctx.agent.currency))
    confirmed = await confirm(ctx.decider, request, details, ctx.agent_said, ctx.visitor_said)
    if confirmed is None:
        return fail(
            "Not sent: the visitor hasn't said a clear yes to a read-back. Read every detail "
            "back, ask for a clear yes, and call submit_request only after it."
        )

    code = await ctx.requests.submit(confirmed)
    left = {k: v for k, v in ctx.state.get("details", {}).items() if k not in have}
    return ToolResult(
        ok=True,
        data={
            "sent": True,
            "code": code,
            "next": f"Tell the visitor the team will get back to them, and give them the code "
            f"{code}: they need it to change or cancel.",
        },
        state={"task": None, "details": left},  # an order in progress keeps its details
    )


TOOL = Tool(
    name="submit_request",
    description=(
        "Send the visitor's message to the team. Call it only after reading every detail back "
        "and hearing a clear yes. Takes no arguments."
    ),
    input_model=SubmitRequestInput,
    run=submit_request,
    label="Sending your message…",
    filler="Sending that now.",
    timeout_s=10.0,  # the commit check is a network call
)
