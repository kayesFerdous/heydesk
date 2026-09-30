"""FakeLLM: plays a script of steps so tests and offline development run free.

A step is the reply text, or an exception to raise. With the script used up it
echoes the visitor. Records every request, so a test can check the prefix
(system + tools) stayed byte-identical.
"""

import asyncio
import json
import re
from collections.abc import AsyncGenerator

from heydesk.ports.llm import (
    ChatRequest,
    Finished,
    Message,
    ModelEvent,
    TextDelta,
    ToolOutcome,
    Usage,
)

type Step = str | Exception


class FakeLLM:
    name = "fake"

    def __init__(self, script: list[Step] | None = None, delay: float = 0.0) -> None:
        self.script = list(script or [])
        self.delay = delay  # seconds between tokens, so the dev page visibly streams
        self.requests: list[ChatRequest] = []

    async def stream(self, request: ChatRequest) -> AsyncGenerator[ModelEvent]:
        self.requests.append(request)
        step = self.script.pop(0) if self.script else _echo(request.messages)
        if isinstance(step, Exception):
            raise step
        words = re.findall(r"\S+\s*", step)
        for word in words:
            await asyncio.sleep(self.delay)
            yield TextDelta(word)
        yield Finished("end", self.assistant_message(step), Usage(output=len(words)))

    def user_message(self, text: str) -> Message:
        return {"role": "user", "content": text}

    def assistant_message(self, text: str) -> Message:
        return {"role": "assistant", "content": text}

    def context_note(self, text: str) -> Message:
        return {"role": "system", "content": text}

    def tool_results(self, results: list[ToolOutcome]) -> list[Message]:
        return [
            {
                "role": "tool",
                "tool_call_id": r.call_id,
                "content": json.dumps(r.data if r.ok else {"error": r.data}),
            }
            for r in results
        ]


def _echo(messages: list[Message]) -> str:
    said = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
    return f"(fake model) You said: {said}"
