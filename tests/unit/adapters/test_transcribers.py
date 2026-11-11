"""Deepgram and Chorus over a mocked HTTP layer: what each sends, the words it gives back, and a
failure as TranscriberUnavailable."""

import httpx
import pytest

from heydesk.adapters.transcriber import chorus
from heydesk.adapters.transcriber.chorus import ChorusTranscriber
from heydesk.adapters.transcriber.deepgram import DeepgramTranscriber
from heydesk.ports.transcriber import TranscriberUnavailable

SAID = "আমি দুইটা লাল টি শার্ট অর্ডার করতে চাই"


async def test_deepgram_hears_a_voice_note_in_the_servers_language() -> None:
    sent: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        alternative = {"transcript": f" {SAID} "}
        return httpx.Response(
            200, json={"results": {"channels": [{"alternatives": [alternative]}]}}
        )

    deepgram = DeepgramTranscriber("key", "bn")
    deepgram.client = httpx.AsyncClient(
        headers=deepgram.client.headers, transport=httpx.MockTransport(answer)
    )
    assert await deepgram.transcribe(b"ogg bytes", "voice.ogg") == SAID
    params = sent[0].url.params
    assert (params["model"], params["language"]) == ("nova-3-general", "bn")
    assert sent[0].headers["Authorization"] == "Token key"
    assert sent[0].content == b"ogg bytes"


async def test_deepgram_refusing_is_unavailable() -> None:
    deepgram = DeepgramTranscriber("bad key", "bn")
    deepgram.client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(401))
    )
    with pytest.raises(TranscriberUnavailable):
        await deepgram.transcribe(b"ogg bytes", "voice.ogg")


@pytest.fixture
def no_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chorus, "POLL_SECONDS", 0)


async def test_chorus_uploads_then_asks_until_the_words_are_ready(no_waiting: None) -> None:
    asked: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            assert b'name="language"\r\n\r\nbn' in request.content
            return httpx.Response(200, json={"task_id": "t1", "status": "queued"})
        asked.append(request.url.path)
        if len(asked) < 3:
            return httpx.Response(200, json={"status": "processing"})
        words = {"transcription": {"text": f" {SAID} "}}
        return httpx.Response(200, json={"status": "completed", "results": words})

    heard = ChorusTranscriber("project", "key", "bn")
    heard.client = httpx.AsyncClient(transport=httpx.MockTransport(answer))
    assert await heard.transcribe(b"ogg bytes", "voice.ogg") == SAID
    assert asked == ["/chorus/v1/speeches/t1"] * 3


async def test_chorus_failing_to_hear_is_unavailable(no_waiting: None) -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"task_id": "t1"})
        return httpx.Response(200, json={"status": "failed"})

    heard = ChorusTranscriber("project", "key", "bn")
    heard.client = httpx.AsyncClient(transport=httpx.MockTransport(answer))
    with pytest.raises(TranscriberUnavailable):
        await heard.transcribe(b"ogg bytes", "voice.ogg")
