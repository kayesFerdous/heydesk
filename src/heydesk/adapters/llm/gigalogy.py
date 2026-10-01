"""GigalogyLLM: Gigalogy's Maira behind the `LanguageModel` port (D14).

Maira is not a chat-completions API, and three things follow from that:

- **One `query`, no message list.** The system prompt and the transcript are
  written into one text, system prompt first. The Maira profile's own prompt
  sits above it, so the profile should say little more than "do what the
  message says".
- **The model is set on the profile**, chosen by `GIGALOGY_PROFILE_ID`, so the
  agent's model name is ignored here.
- **No streaming and no tool calls.** The reply arrives whole and is yielded
  once; tool specs are not sent. Tools need Groq (day 5).

Every call starts a fresh Maira session: we send the whole history ourselves.
"""

from collections.abc import AsyncGenerator
from typing import Any

import httpx

from heydesk.adapters.llm.messages import OpenAIMessages
from heydesk.ports.llm import (
    ChatRequest,
    Finished,
    LLMUnavailable,
    Message,
    ModelEvent,
    TextDelta,
    Usage,
)

BASE_URL = "https://api.maira.gigalogy.com/client"
SPEAKERS = {"user": "Visitor", "assistant": "Agent", "tool": "Tool result"}


class GigalogyLLM(OpenAIMessages):
    name = "gigalogy"

    def __init__(
        self, project_key: str, api_key: str, profile_id: str, timeout: float = 30.0
    ) -> None:
        self.client = httpx.AsyncClient(timeout=timeout)
        self.url = BASE_URL + "/v1/maira/ask"
        self.headers = {"project-key": project_key, "api-key": api_key}
        self.profile_id = profile_id

    async def stream(self, request: ChatRequest) -> AsyncGenerator[ModelEvent]:
        body = {
            "user_id": "heydesk",  # one id for the service; visitors are ours to identify
            "query": build_query(request.system, request.messages),
            "conversation_type": "chat",
            "gpt_profile_id": self.profile_id,
            "platform": "heydesk",
        }
        try:
            response = await self.client.post(self.url, json=body, headers=self.headers)
        except httpx.HTTPError as error:
            raise LLMUnavailable(f"gigalogy: {type(error).__name__}") from error
        detail = read_detail(response)
        text = detail.get("response")
        if not isinstance(text, str) or not text.strip():
            raise LLMUnavailable("gigalogy: no reply text")
        used = detail.get("usage") or {}
        yield TextDelta(text)
        usage = Usage(
            input=used.get("prompt_tokens") or 0, output=used.get("completion_tokens") or 0
        )
        yield Finished("end", self.assistant_message(text), usage)


def build_query(system: str, messages: list[Message]) -> str:
    """The system prompt, then the conversation as labelled lines, then what to do."""
    lines = []
    for m in messages:
        if m.get("content"):
            lines.append(f"{SPEAKERS[m['role']]}: {m['content']}")
    conversation = "\n".join(lines)
    return (
        f"{system.strip()}\n\n# Conversation\n{conversation}\n\n"
        "Write only the Agent's next reply to the Visitor."
    )


def read_detail(response: httpx.Response) -> dict[str, Any]:
    """Maira wraps answers and errors alike in `detail`; the status says which.

    An error keeps Maira's own reason ("Project credential not found.",
    "Profile Not Found!"): it's about our setup, never the visitor's text.
    """
    try:
        data = response.json()
    except ValueError:
        data = None
    detail = data.get("detail") if isinstance(data, dict) else None
    if response.is_error or not isinstance(detail, dict):
        reason = detail.get("response") if isinstance(detail, dict) else detail
        raise LLMUnavailable(f"gigalogy: HTTP {response.status_code}: {str(reason)[:100]}")
    return detail
