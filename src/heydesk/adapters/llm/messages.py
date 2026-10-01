"""The OpenAI chat message format, shared by every adapter here.

Groq speaks it natively; Gigalogy and the fake render or replay it. Using one
format means a conversation's stored transcript works with any of them.
"""

import json

from heydesk.ports.llm import Message, ToolOutcome


class OpenAIMessages:
    def user_message(self, text: str) -> Message:
        return {"role": "user", "content": text}

    def assistant_message(self, text: str) -> Message:
        return {"role": "assistant", "content": text}

    def tool_results(self, results: list[ToolOutcome]) -> list[Message]:
        return [
            {
                "role": "tool",
                "tool_call_id": r.call_id,
                "content": json.dumps(r.data if r.ok else {"error": r.data}),
            }
            for r in results
        ]
