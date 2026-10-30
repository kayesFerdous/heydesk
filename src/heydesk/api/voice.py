"""The browser's WebRTC handshake for a voice call; the call itself runs in `voice/` (design §10).

Also the `/phone` page's side: it waits here for its mobile's calls, and answers one by
making the same offer with `call=`."""

import asyncio
from collections.abc import AsyncIterator
from typing import Annotated, Any

import httpx
from aiortc import RTCIceServer
from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.transports.smallwebrtc.request_handler import (
    SmallWebRTCPatchRequest,
    SmallWebRTCRequest,
    SmallWebRTCRequestHandler,
)

from heydesk.api.sse import frame
from heydesk.errors import NoVoice
from heydesk.voice.call import run_call

router = APIRouter(include_in_schema=False)
handler = SmallWebRTCRequestHandler()  # keeps the open peer connections
calls: set[asyncio.Task[None]] = set()  # running calls, so they aren't garbage-collected
TURN_URL = "https://rtc.live.cloudflare.com/v1/turn/keys/{key_id}/credentials/generate-ice-servers"
STUN_ONLY = [{"urls": ["stun:stun.cloudflare.com:3478"]}]
STILL_HERE_S = 15  # a comment this often keeps a tunnel from closing a quiet stream


async def ice_servers(request: Request) -> list[dict[str, Any]]:
    """The relay details for one call: Cloudflare hands out passwords that last a day."""
    settings = request.app.state.settings
    if not settings.CLOUDFLARE_TURN_KEY_ID:
        return STUN_ONLY
    async with httpx.AsyncClient(timeout=10) as client:
        res = await client.post(
            TURN_URL.format(key_id=settings.CLOUDFLARE_TURN_KEY_ID),
            headers={"Authorization": f"Bearer {settings.CLOUDFLARE_TURN_API_TOKEN}"},
            json={"ttl": 86400},
        )
    res.raise_for_status()
    return list(res.json()["iceServers"])


@router.get("/api/voice/ice")
async def ice_for_browser(request: Request) -> list[dict[str, Any]]:
    return await ice_servers(request)


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
        agent = await state.services.agents.by_key(key or state.dev_agent_key)  # 404 if unknown
        if not agent.desk.takes_voice:
            raise NoVoice("This agent takes no calls: chat with it instead.")
        await state.services.visitors.count_turn(
            agent, f"web:{visitor}", count=False
        )  # 429: limited
    handler.update_ice_servers([RTCIceServer(**s) for s in await ice_servers(request)])

    async def handle_call(connection: SmallWebRTCConnection) -> None:
        try:
            await run_call(
                connection, state.settings, state.services, agent, f"web:{visitor}", answered
            )
        finally:
            if answered:  # its outcome, from the order
                await state.services.calls.ended(answered.call_id)

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
    mobile = calls.number(number)  # 400 if it isn't a mobile number

    async def stream() -> AsyncIterator[str]:
        queue = calls.listen(mobile)
        try:
            yield frame("ready", {"number": mobile})
            while True:
                try:
                    ring = await asyncio.wait_for(queue.get(), STILL_HERE_S)
                except TimeoutError:
                    yield ": still here\n\n"
                    continue
                yield frame("ring", ring)
        finally:
            calls.leave(mobile, queue)

    return StreamingResponse(stream(), media_type="text/event-stream")


@router.post("/api/phone/decline", status_code=204)
async def decline(call: str, request: Request) -> None:
    """The buyer tapped Decline: a missed call, tried again later."""
    await request.app.state.services.calls.missed(call)
