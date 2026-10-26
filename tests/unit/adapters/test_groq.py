from collections.abc import AsyncIterator
from typing import Any

from groq.types.chat import ChatCompletionChunk

from heydesk.adapters.llm.groq import read_stream
from heydesk.ports.llm import Finished, ModelEvent, TextDelta, ToolCall, Usage

MODEL = "openai/gpt-oss-120b"


def chunk(
    delta: dict[str, Any], finish: str | None = None, usage: Any = None
) -> ChatCompletionChunk:
    return ChatCompletionChunk.model_validate(
        {
            "id": "c1",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": MODEL,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            "x_groq": {"id": "x", "usage": usage} if usage else None,
        }
    )


async def events(*chunks: ChatCompletionChunk) -> list[ModelEvent]:
    async def stream() -> AsyncIterator[ChatCompletionChunk]:
        for c in chunks:
            yield c

    return [e async for e in read_stream(stream(), MODEL)]


async def test_text_streams_and_usage_is_counted() -> None:
    usage = {
        "prompt_tokens": 1000,
        "completion_tokens": 100,
        "total_tokens": 1100,
    }
    got = await events(
        chunk({"role": "assistant", "content": "Biryani "}),
        chunk({"content": "is ৳450."}),
        chunk({}, finish="stop", usage=usage),
    )

    assert got[:2] == [TextDelta("Biryani "), TextDelta("is ৳450.")]
    # 1000 in × 0.15 + 100 out × 0.75 = 225 millionths of a dollar
    assert got[2] == Finished(
        "end", {"role": "assistant", "content": "Biryani is ৳450."}, Usage(1000, 100, 225)
    )


async def test_cached_input_costs_half() -> None:
    usage = {
        "prompt_tokens": 1000,
        "completion_tokens": 100,
        "total_tokens": 1100,
        "prompt_tokens_details": {"cached_tokens": 800},
    }
    got = await events(chunk({"content": "Hi"}), chunk({}, finish="stop", usage=usage))

    # (200 × 0.15) + (800 × 0.075) + (100 × 0.75) = 30 + 60 + 75
    assert isinstance(got[1], Finished) and got[1].usage == Usage(1000, 100, 165, 800)


async def test_a_tool_call_split_over_chunks_is_joined() -> None:
    def part(**fields: Any) -> dict[str, Any]:
        return {"tool_calls": [{"index": 0, **fields}]}

    got = await events(
        chunk(
            part(
                id="call_1",
                type="function",
                function={"name": "update_order", "arguments": '{"items": '},
            )
        ),
        chunk(part(function={"arguments": "[]}"})),
        chunk({}, finish="tool_calls"),
    )

    assert got[0] == ToolCall("call_1", "update_order", {"items": []})
    finished = got[1]
    assert isinstance(finished, Finished) and finished.stop == "tool_use"
    assert finished.assistant_message["tool_calls"] == [
        {
            "id": "call_1",
            "type": "function",
            "function": {"name": "update_order", "arguments": '{"items": []}'},
        }
    ]


async def test_no_tool_runs_when_the_reply_was_cut_off() -> None:
    got = await events(
        chunk(
            {"tool_calls": [{"index": 0, "id": "c", "function": {"name": "x", "arguments": '{"a'}}]}
        ),
        chunk({}, finish="length"),
    )

    assert len(got) == 1 and isinstance(got[0], Finished) and got[0].stop == "max_tokens"
