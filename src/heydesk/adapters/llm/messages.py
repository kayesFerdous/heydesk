"""The OpenAI chat message format, shared by every adapter here.

Groq speaks it natively; Gigalogy and the fake render or replay it. Using one
format means a conversation's stored transcript works with any of them.
"""

import json

from heydesk.ports.llm import Message, ToolCall, ToolOutcome


class OpenAIMessages:
    def user_message(self, text: str) -> Message:
        return {"role": "user", "content": text}

    def assistant_message(self, text: str) -> Message:
        return {"role": "assistant", "content": text}

    def tool_results(self, results: list[ToolOutcome]) -> list[Message]:
        # A failed tool's data is {"error": hint}; ensure_ascii=False keeps "৳" readable
        return [
            {
                "role": "tool",
                "tool_call_id": r.call_id,
                "content": json.dumps(r.data, ensure_ascii=False),
            }
            for r in results
        ]


def tool_call_message(calls: list[ToolCall]) -> Message:
    """An assistant message that asks for tools, for adapters that build their own."""
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": c.id,
                "type": "function",
                "function": {"name": c.name, "arguments": json.dumps(c.input, ensure_ascii=False)},
            }
            for c in calls
        ],
    }
