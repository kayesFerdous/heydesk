"""A turn's events → text/event-stream frames (design §9)."""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from heydesk.runtime.events import Card, HandOff, Photos, Token, ToolStarted, TurnEnded
from heydesk.services.conversations import RunningTurn
from heydesk.services.live import LiveHub

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
                    yield frame("photos", {"items": items})  # product photos to show
                case Card(kind, data):
                    yield frame("card", {"kind": kind, "data": data})
                case TurnEnded(interrupted=interrupted, choices=choices):
                    if choices:  # buttons under the reply; a tap sends its words
                        yield frame("choices", {"items": choices})
                    yield frame("done", {"interrupted": interrupted})
    except Exception:
        # The response has started, so a 500 would arrive as a silent cut-off.
        log.exception("turn stream failed")
        yield frame("error", {"message": "Something went wrong. Please try again."})


PING_SECONDS = 20


async def live_frames(hub: LiveHub, ws: str) -> AsyncIterator[str]:
    """The inbox's stream: a `changed` frame per chat, and a `ping` while it's quiet."""
    with hub.listen(ws) as queue:
        yield frame("open", {})
        while True:
            try:
                conversation_id = await asyncio.wait_for(queue.get(), PING_SECONDS)
            except TimeoutError:
                yield frame("ping", {})
            else:
                yield frame("changed", {"conversation_id": conversation_id})
