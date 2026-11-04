import asyncio
import json
from typing import Any

from pydantic import BaseModel

from heydesk.ports.llm import ToolCall
from heydesk.runtime import parse
from heydesk.runtime.tool import ArgumentChecker, Tool, ToolContext, ToolResult, run_tool
from heydesk.shop.tools import UPDATE_CART
from tests.conftest import make_ctx

CTX = make_ctx()


class Echo(BaseModel):
    word: str


def make_tool(run: Any, timeout_s: float = 5.0) -> Tool:
    return Tool("echo", "Echoes a word.", Echo, run, timeout_s=timeout_s)


async def echo(ctx: ToolContext[Any], args: Echo) -> ToolResult:
    return ToolResult(ok=True, data={"said": args.word})


async def test_runs_with_validated_arguments() -> None:
    result = await run_tool(make_tool(echo), ToolCall("1", "echo", {"word": "hi"}), CTX)
    assert result == ToolResult(ok=True, data={"said": "hi"})


async def test_the_spec_is_the_input_models_json_schema() -> None:
    spec = make_tool(echo).spec
    assert spec.name == "echo" and spec.input_schema["required"] == ["word"]


async def test_problems_come_back_as_data_never_raised() -> None:
    async def slow(ctx: ToolContext[Any], args: Echo) -> ToolResult:
        await asyncio.sleep(1)
        return ToolResult(ok=True, data={})

    async def broken(ctx: ToolContext[Any], args: Echo) -> ToolResult:
        raise RuntimeError("bug")

    good_call = ToolCall("1", "echo", {"word": "hi"})
    cases = [
        (None, ToolCall("1", "nope", {}), "no tool called 'nope'"),
        (make_tool(echo), ToolCall("1", "echo", {}), "word: Field required"),
        (make_tool(slow, timeout_s=0.01), good_call, "took too long"),
        (make_tool(broken), good_call, "went wrong on our side"),
    ]
    for tool, call, hint in cases:
        result = await run_tool(tool, call, CTX)
        assert not result.ok and hint in result.data["error"]


def test_the_schema_the_model_sees_has_nested_models_written_out() -> None:
    schema = UPDATE_CART.spec.input_schema
    assert "$ref" not in json.dumps(schema) and "$defs" not in schema
    assert schema["properties"]["lines"]["items"]["required"] == ["code", "quantity"]


def test_checks_say_what_is_missing_and_wrong_and_what_to_ask_first() -> None:
    check = ArgumentChecker()
    assert check.take("name", "Kayes", parse.name) == "Kayes"
    assert check.take("pickup_time", "  ", parse.name) is None
    assert check.take("phone", "call me", parse.phone) is None

    result = check.result()
    assert not result.ok
    assert result.data["still_needed"] == ["pickup time"]
    assert "phone number" in result.data["problems"]["phone"]
    assert result.data["next"] == "Ask for the phone, one question at a time."  # problems first
