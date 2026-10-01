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

from heydesk.ports.llm import ToolCall, ToolSpec
from heydesk.runtime.config import AgentConfig
from heydesk.runtime.items import Item
from heydesk.runtime.tasks import Task

log = logging.getLogger(__name__)


class Catalog(Protocol):
    """One agent's items, as tools see them. A service implements it over the database."""

    async def get(self, codes: list[str]) -> dict[str, Item]: ...  # by code; unknown codes left out
    async def search(self, query: str) -> list[Item]: ...


@dataclass(frozen=True)
class ToolContext:
    agent: AgentConfig
    state: dict[str, Any]  # the conversation state so far; return changes in ToolResult.state
    items: Catalog
    tasks: dict[str, Task]  # the preset's tasks
    now: datetime  # when the turn started, in UTC


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    data: dict[str, Any]  # short JSON the model reads
    state: dict[str, Any] | None = None  # changes to the conversation state (the cart, details)


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
