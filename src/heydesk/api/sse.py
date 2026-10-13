"""A turn's events → text/event-stream frames (design §9)."""

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from heydesk.runtime.events import HandOff, Photos, Token, ToolStarted, TurnEnded
from heydesk.services.conversations import RunningTurn

log = logging.getLogger(__name__)


def frame(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def to_sse(turn: RunningTurn) -> AsyncIterator[str]:
    yield frame("open", {"conversation_id": turn.conversation_id})
    try:
        async for event in turn.events:
            match event:
                case Token(text):
                    yield frame("token", {"text": text})
                case ToolStarted(label=label) if label:
                    yield frame("status", {"label": label})  # shown until the reply starts
                case HandOff(message, phone, email):
                    yield frame("handoff", {"message": message, "phone": phone, "email": email})
                case Photos(items):
                    yield frame("photos", {"items": items})  # product photos to show (D57)
                case TurnEnded(interrupted=interrupted):
                    yield frame("done", {"interrupted": interrupted})
    except Exception:
        # The response has started, so a 500 would arrive as a silent cut-off.
        log.exception("turn stream failed")
        yield frame("error", {"message": "Something went wrong. Please try again."})
