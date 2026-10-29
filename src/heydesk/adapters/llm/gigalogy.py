"""GigalogyLLM: Gigalogy's Maira behind the `LanguageModel` port.

Maira is not a chat-completions API, and three things follow from that:

- **One `query`, no message list.** The system prompt, the tools and the
  transcript are written into one text, system prompt first. The Maira
  profile's own prompt sits above it, so the profile should say little more
  than "do what the message says" (its text: docs/prompt-rules.md, section 0).
- **The model is set on the profile**, chosen by `GIGALOGY_PROFILE_ID`, so the
  agent's model name is ignored here.
- **No streaming, and tool calls as JSON in the reply**. The query lists
  the tools and asks for `{"tool": …, "arguments": …}` to use one. The reply
  arrives whole, so we read it before the visitor sees anything: a tool call
  becomes a `ToolCall` like Groq's, and the runtime can't tell the difference.

Every call starts a fresh Maira session: we send the whole history ourselves.
"""

import json
import re
import uuid
from collections.abc import AsyncGenerator
from typing import Any

import httpx

from heydesk.adapters.llm.messages import OpenAIMessages, tool_call_message
from heydesk.ports.llm import (
    ChatRequest,
    Finished,
    LLMUnavailable,
    Message,
    ModelEvent,
    TextDelta,
    ToolCall,
    ToolSpec,
    Usage,
)

BASE_URL = "https://api.maira.gigalogy.com/client"
SPEAKERS = {"user": "Visitor", "assistant": "Agent", "tool": "Tool result"}
TOOL_CALL = re.compile(r'\{\s*"tool"\s*:\s*"([^"]*)"')  # where a tool call starts in a reply


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
            "query": build_query(request.system, request.messages, request.tools),
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
        usage = Usage(
            input=used.get("prompt_tokens") or 0, output=used.get("completion_tokens") or 0
        )
        call = read_tool_call(text) if request.tools else None
        if call:
            yield call
            yield Finished("tool_use", tool_call_message([call]), usage)
        else:
            yield TextDelta(text)
            yield Finished("end", self.assistant_message(text), usage)


def build_query(system: str, messages: list[Message], tools: list[ToolSpec]) -> str:
    """The system prompt, the tools, then the conversation as labelled lines, then what to do."""
    lines = []
    for m in messages:
        if m.get("content"):
            lines.append(f"{SPEAKERS[m['role']]}: {m['content']}")
        for call in m.get("tool_calls") or []:  # shown the way we ask for them, so it's copied
            name, arguments = call["function"]["name"], call["function"]["arguments"]
            lines.append(f'Agent: {{"tool": "{name}", "arguments": {arguments}}}')
    conversation = "\n".join(lines)
    if not tools:
        return (
            f"{system.strip()}\n\n# Conversation\n{conversation}\n\n"
            "Write only the Agent's next reply to the Visitor."
        )
    return (
        f"{system.strip()}\n\n{tools_section(tools)}\n\n# Conversation\n{conversation}\n\n"
        "Write only the Agent's next reply: a message to the Visitor, or one tool call as JSON."
    )


def tools_section(tools: list[ToolSpec]) -> str:
    lines = [
        "# Tools",
        "To use a tool, reply with only this JSON and nothing else:",
        '{"tool": "<name>", "arguments": {<arguments>}}',
        "One tool per reply. You'll see its result, then write your reply to the Visitor.",
    ]
    for tool in tools:
        schema = json.dumps(tool.input_schema, ensure_ascii=False, separators=(",", ":"))
        lines += ["", f"## {tool.name}", tool.description, f"Arguments (JSON Schema): {schema}"]
    return "\n".join(lines)


def read_tool_call(text: str) -> ToolCall | None:
    """The first `{"tool": …}` in the reply, or None for an ordinary reply.

    Words around the JSON are dropped: the visitor never sees them. JSON we
    can't read still becomes a call with no arguments, so the tool's own
    validation tells the model what's missing.
    """
    found = TOOL_CALL.search(text)
    if not found:
        return None
    try:
        parsed, _ = json.JSONDecoder().raw_decode(text, found.start())
    except ValueError:
        parsed = {}
    arguments = parsed.get("arguments") if isinstance(parsed, dict) else None
    call_id = "call_" + uuid.uuid4().hex[:12]
    return ToolCall(call_id, found.group(1), arguments if isinstance(arguments, dict) else {})


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
