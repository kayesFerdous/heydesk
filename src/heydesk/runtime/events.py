"""What a turn yields to its listeners (the SSE response, later the voice pipeline)."""

from dataclasses import dataclass, field
from typing import Any

from heydesk.ports.llm import Message


@dataclass(frozen=True)
class Token:
    text: str


@dataclass(frozen=True)
class ToolStarted:
    name: str
    label: str = ""  # web shows it: "Placing your order…"
    filler: str = ""  # voice says it: "Placing that now."


@dataclass(frozen=True)
class HandedOff:
    message: str
    phone: str | None = None
    email: str | None = None


@dataclass(frozen=True)
class Photos:
    """Photos a tool showed the visitor: each `{name, price, url}`."""

    items: list[dict[str, str]]


@dataclass(frozen=True)
class Card:
    """A card a tool showed the visitor: a summary, a receipt or tracking.
    `data` is built by Python; its shape for each kind is in `runtime/cards.py`."""

    kind: str
    data: dict[str, Any]


@dataclass(frozen=True)
class TurnEnded:
    added: list[Message]  # transcript additions, provider-native, in order
    state: dict[str, Any]  # the conversation state after the turn
    trace: dict[str, Any]
    handed_off: bool = False
    interrupted: bool = False
    alerts: list[str] = field(default_factory=list)  # tools' alerts for the owner
    tools: list[str] = field(default_factory=list)  # the tools that ran, in order
    choices: list[str] = field(default_factory=list)  # the last tool's buttons


type Event = Token | ToolStarted | HandedOff | Photos | Card | TurnEnded
