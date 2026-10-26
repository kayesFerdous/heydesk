"""The `LanguageModel` port: talks and calls tools (design §6).

The runtime deals in these neutral types. The transcript stays in the
provider's own message format; adapters build those messages so the runtime
never has to.
"""

from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any, Literal, Protocol

type Message = dict[str, Any]  # provider-native; stored and resent as-is


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]  # tool.input_model.model_json_schema()


@dataclass(frozen=True)
class ChatRequest:
    system: str  # built for this turn: rules, role, knowledge, today's date
    tools: list[ToolSpec]
    messages: list[Message]  # the conversation so far, then this turn's additions
    model: str


@dataclass(frozen=True)
class Usage:
    input: int = 0
    output: int = 0
    cost_micros: int = 0  # millionths of a dollar
    cached: int = 0  # input tokens the provider had cached, part of `input`


# What a stream yields
@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]


@dataclass(frozen=True)
class Finished:
    stop: Literal["end", "tool_use", "max_tokens", "refusal"]
    assistant_message: Message  # append to the transcript as-is
    usage: Usage


type ModelEvent = TextDelta | ToolCall | Finished


@dataclass(frozen=True)
class ToolOutcome:
    """A tool's result as the model sees it."""

    call_id: str
    ok: bool
    data: Any


class LanguageModel(Protocol):
    name: str

    def stream(self, request: ChatRequest) -> AsyncGenerator[ModelEvent]: ...

    # Provider-native messages, so the runtime never touches the format:
    def user_message(self, text: str) -> Message: ...
    def assistant_message(self, text: str) -> Message: ...  # a scripted line the visitor saw
    def tool_results(self, results: list[ToolOutcome]) -> list[Message]: ...


class LLMUnavailable(Exception):
    """Any provider error or timeout. The turn becomes a polite hand-off."""
