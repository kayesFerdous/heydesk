import json

import httpx
import pytest

from heydesk.adapters.llm.gigalogy import GigalogyLLM, build_query, read_tool_call
from heydesk.adapters.llm.messages import tool_call_message
from heydesk.ports.llm import (
    ChatRequest,
    Finished,
    LLMUnavailable,
    TextDelta,
    ToolCall,
    ToolSpec,
    Usage,
)

MESSAGES = [
    {"role": "assistant", "content": "Hi, welcome!"},
    {"role": "user", "content": "What's good?"},
]
REQUEST = ChatRequest("SYSTEM", [], MESSAGES, model="ignored")


def llm_answering(
    status: int, body: object, seen: list[httpx.Request] | None = None
) -> GigalogyLLM:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, json=body)

    llm = GigalogyLLM("project", "key", "profile")
    llm.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return llm


def test_the_query_is_the_system_prompt_then_the_conversation() -> None:
    assert build_query("SYSTEM", MESSAGES, []) == (
        "SYSTEM\n\n# Conversation\nAgent: Hi, welcome!\n"
        "Visitor: What's good?\n\nWrite only the Agent's next reply to the Visitor."
    )


async def test_the_reply_comes_whole_with_its_usage() -> None:
    seen: list[httpx.Request] = []
    answer = {
        "detail": {
            "response": "Try the biryani.",
            "usage": {"prompt_tokens": 50, "completion_tokens": 5},
        }
    }
    events = [e async for e in llm_answering(200, answer, seen).stream(REQUEST)]

    assert events == [
        TextDelta("Try the biryani."),
        Finished(
            "end", {"role": "assistant", "content": "Try the biryani."}, Usage(input=50, output=5)
        ),
    ]
    sent = seen[0]
    assert sent.headers["project-key"] == "project" and sent.headers["api-key"] == "key"
    assert json.loads(sent.content)["gpt_profile_id"] == "profile"


async def test_an_error_or_an_empty_reply_is_unavailable() -> None:
    answer = {"detail": {"response": "Project credential not found."}}
    with pytest.raises(LLMUnavailable, match="HTTP 401: Project credential not found"):
        [e async for e in llm_answering(401, answer).stream(REQUEST)]
    with pytest.raises(LLMUnavailable, match="no reply text"):
        [e async for e in llm_answering(200, {"detail": {"response": " "}}).stream(REQUEST)]


SEARCH = ToolSpec("search_items", "Find items.", {"type": "object"})


def answering(text: str) -> dict[str, object]:
    return {"detail": {"response": text}}


def test_with_tools_the_query_lists_them_and_shows_past_calls_as_json() -> None:
    history = [
        *MESSAGES,
        tool_call_message([ToolCall("call_1", "search_items", {"query": "rice"})]),
        {"role": "tool", "tool_call_id": "call_1", "content": '{"items": []}'},
    ]
    query = build_query("SYSTEM", history, [SEARCH])

    assert query.startswith("SYSTEM\n\n# Tools\nTo use a tool, reply with only this JSON")
    assert '## search_items\nFind items.\nArguments (JSON Schema): {"type":"object"}' in query
    assert (
        'Agent: {"tool": "search_items", "arguments": {"query": "rice"}}\n'
        'Tool result: {"items": []}' in query
    )
    assert query.endswith("a message to the Visitor, or one tool call as JSON.")


async def test_a_json_reply_becomes_a_tool_call() -> None:
    reply = 'Sure! {"tool": "search_items", "arguments": {"query": "rice"}}'
    request = ChatRequest("SYSTEM", [SEARCH], MESSAGES, model="ignored")
    events = [e async for e in llm_answering(200, answering(reply)).stream(request)]

    call, finished = events  # no text: the words around the JSON are dropped
    assert isinstance(call, ToolCall) and call.name == "search_items"
    assert call.input == {"query": "rice"} and call.id.startswith("call_")
    assert isinstance(finished, Finished) and finished.stop == "tool_use"
    assert finished.assistant_message == tool_call_message([call])


def test_reading_tool_calls() -> None:
    assert read_tool_call("Try the biryani, it's great.") is None
    fenced = read_tool_call('```json\n{"tool": "search_items", "arguments": {"query": "x"}}\n```')
    assert fenced and fenced.input == {"query": "x"}
    broken = read_tool_call('{"tool": "update_order", "arguments": {"lines": [')
    assert broken and broken.name == "update_order" and broken.input == {}  # validation says why


async def test_without_tools_json_is_just_text() -> None:
    reply = '{"tool": "search_items"}'
    events = [e async for e in llm_answering(200, answering(reply)).stream(REQUEST)]
    assert events[0] == TextDelta(reply)
