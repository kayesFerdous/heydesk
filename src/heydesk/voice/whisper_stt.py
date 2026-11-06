"""Speech-to-text with our Bangla–English Whisper on a Kaggle GPU: one upload per utterance.

`tts_server/kaggle_stt.ipynb` serves `kayees/whisper-medium-bn-en-cs-faster` at
`WHISPER_STT_URL`. Like Chorus, it isn't a live stream: Pipecat's `SegmentedSTTService`
collects the audio while the caller talks and hands us a WAV when the VAD says they stopped.
"""

import time
from collections.abc import AsyncGenerator

import httpx
from loguru import logger
from pipecat.frames.frames import ErrorFrame, Frame, TranscriptionFrame
from pipecat.services.stt_service import SegmentedSTTService
from pipecat.utils.time import time_now_iso8601

TIMEOUT_SECONDS = 15.0


class WhisperSTT(SegmentedSTTService):
    def __init__(self, url: str, language: str) -> None:
        super().__init__()
        self.language = language
        self.client = httpx.AsyncClient(base_url=url, timeout=TIMEOUT_SECONDS)

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame | None]:
        started = time.perf_counter()
        try:
            response = await self.client.post(
                "/transcribe",
                params={"language": self.language},
                content=audio,
                headers={"Content-Type": "audio/wav"},
            )
            response.raise_for_status()
            text = str(response.json()["text"]).strip()
        except (httpx.HTTPError, KeyError, ValueError) as error:  # logged; say it again
            yield ErrorFrame(error=f"whisper: {type(error).__name__}: {error}")
            return
        logger.info(f"whisper: {time.perf_counter() - started:.2f}s")
        if text:
            yield TranscriptionFrame(text, self._user_id, time_now_iso8601())

    async def cleanup(self) -> None:
        await super().cleanup()
        await self.client.aclose()
