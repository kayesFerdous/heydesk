"""What a turn yields to its listeners (the SSE response, later the voice pipeline)."""

from dataclasses import dataclass
from typing import Any

from heydesk.ports.llm import Message


@dataclass(frozen=True)
class Token:
    text: str


@dataclass(frozen=True)
class Progress:
    """A scripted waiting line while a tool runs (D6). Never enters the transcript.

    Web shows `label` ("Checking the menu…"); voice says `text` ("Let me check that.").
    """

    label: str
    text: str = ""


@dataclass(frozen=True)
class ToolStarted:
    name: str
    label: str = ""  # web shows it: "Placing your order…"
    filler: str = ""  # voice says it: "Placing that now."


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
    state: dict[str, Any]  # the conversation state after the turn
    trace: dict[str, Any]
    handed_off: bool = False
    interrupted: bool = False


type Event = Token | Progress | ToolStarted | ToolFinished | HandOff | TurnEnded
