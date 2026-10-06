"""The tool contract (design §7). The tools themselves live in `tools/`.

`run_tool` never raises: bad arguments, a timeout or a bug all come back as
`ok=False` with a hint, so the model can recover or offer the hand-off.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from pydantic import BaseModel, ValidationError

from heydesk.domain.values import (
    Appointment,
    AppointmentChange,
    Confirmed,
    NewOrder,
    NewRequest,
)
from heydesk.ports.decider import Decider
from heydesk.ports.knowledge_store import Snippet
from heydesk.ports.llm import ToolCall, ToolSpec
from heydesk.runtime.config import AgentConfig
from heydesk.runtime.items import Item
from heydesk.runtime.tasks import Task

log = logging.getLogger(__name__)


class Catalog(Protocol):
    """One agent's items, as tools see them. A service implements it over the database."""

    async def get(self, codes: list[str]) -> dict[str, Item]: ...  # by code; unknown codes left out
    async def all(self) -> list[Item]: ...  # in the business's order


class Knowledge(Protocol):
    """One agent's documents, searched (D35). A service implements it over Qdrant."""

    async def search(self, question: str) -> list[Snippet]: ...  # the best few, most relevant first


class Orders(Protocol):
    """Where one conversation's orders are saved. Only a confirmed order gets in (rule 2)."""

    async def place(self, order: Confirmed[NewOrder]) -> int: ...  # the order number


class Requests(Protocol):
    """Where one conversation's messages for the team are saved. Only confirmed ones (rule 2)."""

    async def submit(self, request: Confirmed[NewRequest]) -> str: ...  # its code: "A-104"


class Appointments(Protocol):
    """A business's appointment requests, found by number; changed only when confirmed."""

    async def find(self, number: int) -> Appointment | None: ...
    async def change(self, change: Confirmed[AppointmentChange]) -> None: ...


@dataclass(frozen=True)
class ToolContext:
    agent: AgentConfig
    state: dict[str, Any]  # the conversation state so far; return changes in ToolResult.state
    items: Catalog
    knowledge: Knowledge
    tasks: dict[str, Task]  # the agent's jobs that are on
    now: datetime  # when the turn started, in UTC
    decider: Decider  # for the commit check
    orders: Orders
    requests: Requests
    appointments: Appointments
    agent_said: str  # the agent's last message before this turn: the read-back, if there was one
    visitor_said: str  # this turn's message: the yes, if there was one


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    data: dict[str, Any]  # short JSON the model reads
    state: dict[str, Any] | None = None  # changes to the conversation state (the cart, details)
    handoff: bool = False  # the turn ends with the hand-off card; the conversation needs a person


def fail(hint: str) -> ToolResult:
    """A problem the model can act on: what went wrong and what to do next."""
    return ToolResult(ok=False, data={"error": hint})


@dataclass(frozen=True)
class Tool:
    name: str
    description: str  # for the model: when to use it, what it returns
    input_model: type[BaseModel]  # becomes the JSON Schema the model sees
    run: Callable[[ToolContext, Any], Awaitable[ToolResult]]  # gets an `input_model` instance
    label: str = ""  # web status line while it runs: "Checking the menu…"
    filler: str = ""  # voice line at the start: "Let me check that."
    timeout_s: float = 5.0

    @property
    def spec(self) -> ToolSpec:
        schema = inline_refs(self.input_model.model_json_schema())
        return ToolSpec(self.name, self.description, schema)


def inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Writes each nested model out where it's used, instead of pydantic's `$defs` + `$ref`.

    gpt-oss doesn't follow a `$ref`: it guessed the field names (`item_code` for `code`).
    """
    defs = schema.get("$defs", {})

    def expand(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:  # "#/$defs/OrderLine"
                return expand(defs[node["$ref"].split("/")[-1]])
            return {key: expand(value) for key, value in node.items() if key != "$defs"}
        if isinstance(node, list):
            return [expand(value) for value in node]
        return node

    result: dict[str, Any] = expand(schema)
    return result


async def run_tool(tool: Tool | None, call: ToolCall, ctx: ToolContext) -> ToolResult:
    if tool is None:
        return fail(f"There's no tool called {call.name!r}. Use only the tools you were given.")
    try:
        args = tool.input_model.model_validate(call.input)
    except ValidationError as error:
        problems = "; ".join(
            f"{'.'.join(map(str, e['loc'])) or 'input'}: {e['msg']}" for e in error.errors()
        )
        return fail(f"The arguments don't fit: {problems}. Fix them and call {tool.name} again.")
    try:
        async with asyncio.timeout(tool.timeout_s):
            return await tool.run(ctx, args)
    except TimeoutError:
        return fail("That took too long. Try once more; if it fails again, offer the hand-off.")
    except Exception:
        log.exception("tool failed", extra={"tool": tool.name})
        return fail("Something went wrong on our side. Offer to pass this to the team.")
