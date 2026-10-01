import json

import httpx
import pytest

from heydesk.adapters.llm.gigalogy import GigalogyLLM, build_query
from heydesk.ports.llm import ChatRequest, Finished, LLMUnavailable, TextDelta, Usage

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
    assert build_query("SYSTEM", MESSAGES) == (
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
    with pytest.raises(LLMUnavailable, match="HTTP 401"):
        [
            e
            async for e in llm_answering(401, {"detail": "Project credential not found"}).stream(
                REQUEST
            )
        ]
    with pytest.raises(LLMUnavailable, match="no reply text"):
        [e async for e in llm_answering(200, {"detail": {"response": " "}}).stream(REQUEST)]
