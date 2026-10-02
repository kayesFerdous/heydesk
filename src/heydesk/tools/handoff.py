"""`handoff_to_human`: passes the conversation to a person on the team."""

from pydantic import BaseModel

from heydesk.runtime.tool import Tool, ToolContext, ToolResult


class HandoffInput(BaseModel):
    """No arguments."""


async def handoff_to_human(ctx: ToolContext, args: HandoffInput) -> ToolResult:
    contact = ctx.agent.handoff
    data = {"message": contact.message, "phone": contact.phone, "email": contact.email}
    # The page shows these on a card after the reply; the conversation is marked needs_human
    return ToolResult(ok=True, data={k: v for k, v in data.items() if v}, handoff=True)


TOOL = Tool(
    name="handoff_to_human",
    description=(
        "Pass the conversation to a person on the team: when the visitor asks for a person, "
        "or you can't help. Takes no arguments. Then tell the visitor the message it returns."
    ),
    input_model=HandoffInput,
    run=handoff_to_human,
    label="Getting someone from the team…",
    filler="Sure, one moment.",
)
