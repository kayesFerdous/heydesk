"""The browser's WebRTC handshake for a voice call; the call itself runs in `voice/` (design §10)."""

import asyncio
from typing import Annotated, Any

import httpx
from aiortc import RTCIceServer
from fastapi import APIRouter, Query, Request
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.transports.smallwebrtc.request_handler import (
    SmallWebRTCPatchRequest,
    SmallWebRTCRequest,
    SmallWebRTCRequestHandler,
)

from heydesk.api.deps import client_ip
from heydesk.voice.call import run_call

router = APIRouter(include_in_schema=False)
handler = SmallWebRTCRequestHandler()  # keeps the open peer connections
calls: set[asyncio.Task[None]] = set()  # running calls, so they aren't garbage-collected
TURN_URL = "https://rtc.live.cloudflare.com/v1/turn/keys/{key_id}/credentials/generate-ice-servers"
STUN_ONLY = [{"urls": ["stun:stun.cloudflare.com:3478"]}]


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
) -> dict[str, Any] | None:
    """`visitor` is the page's token, as in web chat: a call shares the visitor's limits (D51)."""
    state = request.app.state
    await state.services.rate_limits.call(client_ip(request))  # 429 for a burst of calls (D52)
    agent = await state.services.agents.by_key(key or state.dev_agent_key)  # 404 if unknown
    await state.services.visitors.admit(agent, f"web:{visitor}", count=False)  # 429 if limited
    handler.update_ice_servers([RTCIceServer(**s) for s in await ice_servers(request)])

    async def start(connection: SmallWebRTCConnection) -> None:
        call = asyncio.create_task(
            run_call(connection, state.settings, state.services, agent, f"web:{visitor}")
        )
        calls.add(call)
        call.add_done_callback(calls.discard)

    return await handler.handle_web_request(body, start)


@router.patch("/api/voice/offer")
async def ice(body: SmallWebRTCPatchRequest) -> None:
    await handler.handle_patch_request(body)
