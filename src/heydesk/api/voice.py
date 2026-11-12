"""The browser's WebRTC handshake for a voice call; the call itself runs in `voice/` (design §10).

Also the `/phone` page's side: it waits here for its mobile's calls, and answers one by
making the same offer with `call=`."""

import asyncio
from collections.abc import AsyncIterator
from typing import Annotated, Any

from aiortc import RTCIceServer
from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.transports.smallwebrtc.request_handler import (
    SmallWebRTCPatchRequest,
    SmallWebRTCRequest,
    SmallWebRTCRequestHandler,
)

from heydesk import shop
from heydesk.api.sse import frame
from heydesk.errors import NoVoice
from heydesk.voice.call import run_call
from heydesk.voice.ice import ice_servers

router = APIRouter(include_in_schema=False)
handler = SmallWebRTCRequestHandler()  # keeps the open peer connections
calls: set[asyncio.Task[None]] = set()  # running calls, so they aren't garbage-collected
STILL_HERE_SECONDS = 15  # a comment this often keeps a tunnel from closing a quiet stream


@router.get("/api/voice/ice")
async def ice_for_browser(request: Request) -> list[dict[str, Any]]:
    return await ice_servers(request.app.state.settings)


@router.post("/api/voice/offer")
async def offer(
    body: SmallWebRTCRequest,
    request: Request,
    visitor: Annotated[str, Query(min_length=8, max_length=64)],
    key: str | None = None,
    call: str | None = None,
) -> dict[str, Any] | None:
    """`visitor` is the page's token, as in web chat: a call shares the visitor's limits.
    `call` is a ring the `/phone` page answered: the shop's call to confirm an order."""
    state = request.app.state
    answered = await state.services.calls.answer(call) if call else None  # 404 if it ended
    if answered:
        agent = answered.agent
    else:
        agent = await state.services.agents.by_public_key(
            key or state.dev_agent_key
        )  # 404 if unknown
        if not shop.TAKES_VOICE:
            raise NoVoice("This agent takes no calls: chat with it instead.")
        await state.services.visitors.count_turn(
            agent, f"web:{visitor}", count=False
        )  # 429: limited
    handler.update_ice_servers([RTCIceServer(**s) for s in await ice_servers(state.settings)])

    async def handle_call(connection: SmallWebRTCConnection) -> None:
        try:
            await run_call(
                connection, state.settings, state.services, agent, f"web:{visitor}", answered
            )
        finally:
            if answered:  # its outcome, from the order
                await state.services.calls.mark_ended(answered.call_id)

    async def start(connection: SmallWebRTCConnection) -> None:
        call = asyncio.create_task(handle_call(connection))
        calls.add(call)
        call.add_done_callback(calls.discard)

    return await handler.handle_web_request(body, start)


@router.patch("/api/voice/offer")
async def ice(body: SmallWebRTCPatchRequest) -> None:
    await handler.handle_patch_request(body)


@router.get("/api/phone/rings")
async def rings(number: str, request: Request) -> StreamingResponse:
    """The `/phone` page waits here for its mobile's calls: `ready {number}`, then a
    `ring {call, business, order}` for each one."""
    calls = request.app.state.services.calls
    mobile = calls.parse_mobile_number(number)  # 400 if it isn't a mobile number

    async def stream() -> AsyncIterator[str]:
        queue = calls.wait_for_calls(mobile)
        try:
            yield frame("ready", {"number": mobile})
            while True:
                try:
                    ring = await asyncio.wait_for(queue.get(), STILL_HERE_SECONDS)
                except TimeoutError:
                    yield ": still here\n\n"
                    continue
                yield frame("ring", ring)
        finally:
            calls.stop_waiting(mobile, queue)

    return StreamingResponse(stream(), media_type="text/event-stream")


@router.post("/api/phone/decline", status_code=204)
async def decline(call: str, request: Request) -> None:
    """The buyer tapped Decline: a missed call, tried again later."""
    await request.app.state.services.calls.mark_missed(call)
