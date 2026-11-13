"""The relay a voice call's audio travels through when the browser is far away: Cloudflare's
TURN service, asked for fresh details for each call."""

from typing import Any

import httpx

from heydesk.config import Settings

TURN_URL = "https://rtc.live.cloudflare.com/v1/turn/keys/{key_id}/credentials/generate-ice-servers"
STUN_ONLY = [{"urls": ["stun:stun.cloudflare.com:3478"]}]


async def ice_servers(settings: Settings) -> list[dict[str, Any]]:
    """The relay details for one call: Cloudflare hands out passwords that last a day. With no
    relay set up, only the address finder, which works on the same network."""
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
