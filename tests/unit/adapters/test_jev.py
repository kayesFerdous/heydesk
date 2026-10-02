"""JevDecider over a mock transport: no key, no network, no cost."""

import json
from typing import Any

import httpx2
import pytest
from typesafe_sdk import AsyncTypeSafeClient, RetryPolicy

from heydesk.adapters.decider.jev import JevDecider
from heydesk.ports.decider import DeciderUnavailable, YesNo

QUESTIONS = {"said_yes": YesNo("Is visitor_reply a clear yes?")}


def jev(status: int, body: dict[str, Any], seen: list[Any] | None = None) -> JevDecider:
    def handle(request: httpx2.Request) -> httpx2.Response:
        if seen is not None:
            seen.append(json.loads(request.content))
        return httpx2.Response(status, json=body)

    client = AsyncTypeSafeClient(
        api_key="test-key", transport=httpx2.MockTransport(handle), retry=RetryPolicy(max_retries=0)
    )
    return JevDecider(client, "jev-latest")


async def test_a_yes_no_is_a_noul_and_comes_back_as_a_probability() -> None:
    seen: list[Any] = []
    answer = {"type": "noul", "noul": 0.9}
    body = {"model": "jev-1", "answers": {"said_yes": answer}, "usage": {"input_tokens": 50}}
    answers = await jev(200, body, seen).decide({"visitor_reply": "yes"}, QUESTIONS)

    assert answers["said_yes"].p_yes == pytest.approx(0.9)
    assert answers["said_yes"].likely(0.7)
    [sent] = seen
    assert sent["state"] == {"visitor_reply": "yes"}
    assert sent["questions"] == {
        "said_yes": {"type": "noul", "instructions": "Is visitor_reply a clear yes?"}
    }


async def test_an_answer_left_out_is_not_a_yes() -> None:
    body = {"model": "jev-1", "answers": {}, "usage": {}}
    answers = await jev(200, body).decide({}, QUESTIONS)
    assert not answers["said_yes"].likely()


async def test_an_error_is_decider_unavailable() -> None:
    with pytest.raises(DeciderUnavailable):
        await jev(500, {"error": "down"}).decide({}, QUESTIONS)
