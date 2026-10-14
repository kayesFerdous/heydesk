"""The tool contract (design §7). The tools themselves live in each desk's `tools.py`.

`run_tool` never raises: bad arguments, a timeout or a bug all come back as
`ok=False` with a hint, so the model can recover or offer the hand-off.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ValidationError

from heydesk.ports.decider import Decider
from heydesk.ports.llm import ToolCall, ToolSpec
from heydesk.runtime.config import AgentConfig

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolContext[S]:
    """What a tool gets. `store` is its desk's data (a `ClinicStore`, a `RestaurantStore`):
    the only way a tool reaches the database, always scoped to one business."""

    agent: AgentConfig  # the desk's own config class
    store: S
    state: dict[str, Any]  # the conversation state so far; return changes in ToolResult.state
    now: datetime  # when the turn started, in UTC
    decider: Decider  # for the commit check
    agent_said: str  # the agent's last message before this turn: the read-back, if there was one
    visitor_said: str  # this turn's message: the yes, if there was one
    channel: str = (
        "web"  # "web", "voice", "telegram", "messenger": only the web chat shows photos (D57)
    )

    @property
    def local_now(self) -> datetime:
        """Now in the business's timezone: what "today" and "7 pm" mean."""
        return self.now.astimezone(ZoneInfo(self.agent.timezone))


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    data: dict[str, Any]  # short JSON the model reads
    state: dict[str, Any] | None = None  # changes to the conversation state (the cart)
    handoff: bool = False  # the turn ends with the hand-off card; the conversation needs a person
    alert: str | None = None  # for the owner, on Telegram (D43): what was just saved
    reply: str | None = None  # said to the visitor as it is, and the turn ends: no more model calls
    photos: list[dict[str, str]] | None = None  # shown to the visitor: {name, price, url} (D57)


def fail(hint: str) -> ToolResult:
    """A problem the model can act on: what went wrong and what to do next."""
    return ToolResult(ok=False, data={"error": hint})


class Checks:
    """Checks a tool's details one by one and remembers what's missing or wrong, so the model
    hears about all of it at once and knows what to ask next. A tool takes each detail with
    `take`, and if any came back None, returns `result()`."""

    def __init__(self) -> None:
        self.missing: list[str] = []
        self.problems: dict[str, str] = {}

    def take[T](self, detail: str, said: str, parse: Callable[[str], T]) -> T | None:
        """`parse(said)`, or None if `said` is empty (missing) or `parse` refused it."""
        if not said.strip():
            self.missing.append(detail.replace("_", " "))
            return None
        try:
            return parse(said)
        except ValueError as error:
            self.problems[detail.replace("_", " ")] = str(error)
            return None

    def result(self) -> ToolResult:
        first = next(iter(self.problems), None) or self.missing[0]
        data: dict[str, Any] = {}
        if self.problems:
            data["problems"] = self.problems
        if self.missing:
            data["still_needed"] = self.missing
        data["next"] = f"Ask for the {first}, one question at a time."
        return ToolResult(ok=False, data=data)


@dataclass(frozen=True)
class Tool:
    name: str
    description: str  # for the model: when to use it, what it returns
    input_model: type[BaseModel]  # becomes the JSON Schema the model sees
    run: Callable[[ToolContext[Any], Any], Awaitable[ToolResult]]  # gets an `input_model` instance
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


async def run_tool(tool: Tool | None, call: ToolCall, ctx: ToolContext[Any]) -> ToolResult:
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
