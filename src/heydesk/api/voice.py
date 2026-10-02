"""The browser's WebRTC handshake for a voice call; the call itself runs in `voice/` (design §10)."""

import asyncio
from typing import Any

from fastapi import APIRouter, Request
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.transports.smallwebrtc.request_handler import (
    SmallWebRTCPatchRequest,
    SmallWebRTCRequest,
    SmallWebRTCRequestHandler,
)

from heydesk.voice.call import run_call

router = APIRouter(include_in_schema=False)
handler = SmallWebRTCRequestHandler()  # keeps the open peer connections
calls: set[asyncio.Task[None]] = set()  # running calls, so they aren't garbage-collected


@router.post("/api/voice/offer")
async def offer(
    body: SmallWebRTCRequest, request: Request, key: str | None = None
) -> dict[str, Any] | None:
    state = request.app.state
    agent = await state.services.agents.by_key(key or state.dev_agent_key)  # 404 if unknown

    async def start(connection: SmallWebRTCConnection) -> None:
        call = asyncio.create_task(run_call(connection, state.settings, state.services, agent))
        calls.add(call)
        call.add_done_callback(calls.discard)

    return await handler.handle_web_request(body, start)


@router.patch("/api/voice/offer")
async def ice(body: SmallWebRTCPatchRequest) -> None:
    await handler.handle_patch_request(body)
