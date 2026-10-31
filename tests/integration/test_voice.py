"""Voice is off for the shop for now: the chat page's info says so, and a call is refused
before it starts. With voice on, a call starts."""

from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from heydesk.api import voice
from heydesk.desks.shop import ShopDesk
from tests.conftest import DEV_KEY

OFFER = {"sdp": "v=0", "type": "offer"}  # the browser's offer; the handshake itself is faked
CALL = {"key": DEV_KEY, "visitor": "test-visitor"}


@pytest.fixture
def calls(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The voice routes (served only in development) on the test app, with the handshake
    faked: it notes each call that starts."""
    app.include_router(voice.router)
    monkeypatch.setattr(app.state.settings, "CLOUDFLARE_TURN_KEY_ID", "")  # no relay
    started: list[str] = []

    async def handshake(body: Any, start: Any) -> dict[str, str]:
        started.append("call")
        return {"sdp": "v=0", "type": "answer"}

    monkeypatch.setattr(voice.handler, "handle_web_request", handshake)
    return started


async def test_a_call_starts_when_the_desk_takes_voice(
    client: AsyncClient, calls: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ShopDesk, "takes_voice", True)
    assert (await client.get(f"/api/chat/{DEV_KEY}")).json()["voice"] is True

    res = await client.post("/api/voice/offer", params=CALL, json=OFFER)
    assert (res.status_code, calls) == (200, ["call"])


async def test_a_call_to_the_shop_is_refused_for_now(client: AsyncClient, calls: list[str]) -> None:
    assert (await client.get(f"/api/chat/{DEV_KEY}")).json()["voice"] is False  # no Call button

    res = await client.post("/api/voice/offer", params=CALL, json=OFFER)
    assert (res.status_code, res.json()["error"]["code"]) == (403, "no_voice")
    assert calls == []
