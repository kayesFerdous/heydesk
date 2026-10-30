"""Voice is only for the receptionist and the clinic: the chat page's info says so,
and a call to any other desk is refused before it starts."""

from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from heydesk.api import voice
from tests.conftest import SEEDS

OFFER = {"sdp": "v=0", "type": "offer"}  # the browser's offer; the handshake itself is faked


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


async def test_a_clinic_call_starts(app: FastAPI, client: AsyncClient, calls: list[str]) -> None:
    await app.state.services.agents.ensure_dev_agent("clinic", SEEDS / "clinic.yaml")
    assert (await client.get("/api/chat/dev-clinic")).json()["voice"] is True

    res = await client.post(
        "/api/voice/offer", params={"key": "dev-clinic", "visitor": "test-visitor"}, json=OFFER
    )
    assert (res.status_code, calls) == (200, ["call"])


@pytest.mark.parametrize("kind", ["shop", "restaurant"])
async def test_a_shop_or_restaurant_call_is_refused(
    app: FastAPI, client: AsyncClient, calls: list[str], kind: str
) -> None:
    await app.state.services.agents.ensure_dev_agent(kind, SEEDS / f"{kind}.yaml")
    assert (await client.get(f"/api/chat/dev-{kind}")).json()["voice"] is False  # no Call button

    res = await client.post(
        "/api/voice/offer", params={"key": f"dev-{kind}", "visitor": "test-visitor"}, json=OFFER
    )
    assert (res.status_code, res.json()["error"]["code"]) == (403, "no_voice")
    assert calls == []
