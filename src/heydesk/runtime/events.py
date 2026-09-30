"""What a turn yields to its listeners (the SSE response, later the voice pipeline)."""

from dataclasses import dataclass
from typing import Any, Literal

from heydesk.ports.llm import Message


@dataclass(frozen=True)
class Token:
    text: str


@dataclass(frozen=True)
class Progress:
    """A scripted status line (07 §3). Never enters the transcript.

    Web shows `label` ("Checking availability…"); voice speaks `text`.
    """

    kind: Literal["ack", "wait", "long_wait"]
    label: str
    text: str | None = None


@dataclass(frozen=True)
class ToolStarted:
    name: str
    label: str = ""


@dataclass(frozen=True)
class ToolFinished:
    name: str
    ok: bool


@dataclass(frozen=True)
class HandOff:
    message: str
    phone: str | None = None
    email: str | None = None


@dataclass(frozen=True)
class TurnEnded:
    added: list[Message]  # transcript additions, provider-native, in order
    trace: dict[str, Any]
    handed_off: bool = False
    interrupted: bool = False


type Event = Token | Progress | ToolStarted | ToolFinished | HandOff | TurnEnded
