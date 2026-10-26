"""GroqLLM: streams replies and tool calls from Groq (design §6)."""

import json
from collections.abc import AsyncGenerator, AsyncIterable
from typing import Any

from groq import AsyncGroq, GroqError, omit
from groq.types.chat import ChatCompletionChunk
from groq.types.completion_usage import CompletionUsage

from heydesk.adapters.llm.messages import OpenAIMessages
from heydesk.ports.llm import (
    ChatRequest,
    Finished,
    LLMUnavailable,
    ModelEvent,
    TextDelta,
    ToolCall,
    ToolSpec,
    Usage,
)

# Dollars per million tokens (input, output)
PRICES = {"openai/gpt-oss-120b": (0.15, 0.75), "openai/gpt-oss-20b": (0.075, 0.30)}
CACHED = 0.5  # cached input costs half (Groq's prompt caching)

STOPS: dict[str, Any] = {
    "stop": "end",
    "tool_calls": "tool_use",
    "length": "max_tokens",
    "content_filter": "refusal",
}


class GroqLLM(OpenAIMessages):
    name = "groq"

    def __init__(self, api_key: str, timeout: float = 30.0) -> None:
        self.client = AsyncGroq(api_key=api_key, timeout=timeout, max_retries=1)

    async def stream(self, request: ChatRequest) -> AsyncGenerator[ModelEvent]:
        # Plain dicts in the OpenAI format; Any because the SDK's typed params are much stricter
        messages: Any = [{"role": "system", "content": request.system}, *request.messages]
        tools: Any = [tool_spec(t) for t in request.tools] if request.tools else omit
        try:
            chunks = await self.client.chat.completions.create(
                model=request.model,
                messages=messages,
                tools=tools,
                reasoning_effort="low",  # gpt-oss thinks first; low is enough for a front desk
                include_reasoning=False,  # still counted as output tokens, just not sent
                stream=True,
            )
            async for event in read_stream(chunks, request.model):
                yield event
        except GroqError as error:
            raise LLMUnavailable(f"groq: {error}"[:300]) from error


async def read_stream(
    chunks: AsyncIterable[ChatCompletionChunk], model: str
) -> AsyncGenerator[ModelEvent]:
    """Groq's chunks → our events: text as it comes, tool calls joined at the end."""
    text: list[str] = []
    calls: dict[int, dict[str, str]] = {}  # by index; a call's parts can arrive over several chunks
    stop = "end"
    usage = Usage()
    async for chunk in chunks:
        if found := chunk.usage or (chunk.x_groq and chunk.x_groq.usage):
            usage = to_usage(found, model)
        if not chunk.choices:
            continue
        choice = chunk.choices[0]
        if choice.delta.content:
            text.append(choice.delta.content)
            yield TextDelta(choice.delta.content)
        for part in choice.delta.tool_calls or []:
            call = calls.setdefault(part.index, {"id": "", "name": "", "arguments": ""})
            call["id"] = part.id or call["id"]
            if part.function:
                call["name"] += part.function.name or ""
                call["arguments"] += part.function.arguments or ""
        if choice.finish_reason:
            stop = STOPS.get(choice.finish_reason, "end")

    joined = [calls[i] for i in sorted(calls)]
    if stop != "max_tokens":  # cut off: the arguments may be incomplete, so run nothing
        for call in joined:
            yield ToolCall(call["id"], call["name"], arguments(call["arguments"]))
    message: dict[str, Any] = {"role": "assistant", "content": "".join(text) or None}
    if joined:
        message["tool_calls"] = [
            {
                "id": c["id"],
                "type": "function",
                "function": {"name": c["name"], "arguments": c["arguments"]},
            }
            for c in joined
        ]
    yield Finished(stop, message, usage)  # type: ignore[arg-type]


def tool_spec(tool: ToolSpec) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.input_schema,
        },
    }


def arguments(raw: str) -> dict[str, Any]:
    """Bad JSON becomes {}: the tool's own validation then tells the model what's missing."""
    try:
        parsed = json.loads(raw or "{}")
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def to_usage(usage: CompletionUsage, model: str) -> Usage:
    per_input, per_output = PRICES.get(model, (0.0, 0.0))
    # $ per million tokens = millionths of a dollar per token
    details = usage.prompt_tokens_details
    cached = details.cached_tokens if details else 0
    paid_input = usage.prompt_tokens - cached * CACHED
    cost = paid_input * per_input + usage.completion_tokens * per_output
    return Usage(usage.prompt_tokens, usage.completion_tokens, round(cost), cached)
