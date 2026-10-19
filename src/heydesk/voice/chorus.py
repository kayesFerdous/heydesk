"""Speech-to-text with Gigalogy's Chorus: one upload per utterance, then poll for the text.

Chorus isn't a live stream. Pipecat's `SegmentedSTTService` collects the audio
while the caller talks and hands us a WAV when the VAD says they stopped.
Measured 2 Oct: about 2.4s warm for a 3.5s clip, 6–8s when Chorus is slow.
"""

import asyncio
import time
from collections.abc import AsyncGenerator

import httpx
from loguru import logger
from pipecat.frames.frames import ErrorFrame, Frame, TranscriptionFrame
from pipecat.services.stt_service import SegmentedSTTService
from pipecat.utils.time import time_now_iso8601

URL = "https://api.maira.gigalogy.com/chorus/v1/speeches"
POLL_S = 0.2
TIMEOUT_S = 20.0


class ChorusSTT(SegmentedSTTService):
    def __init__(self, project_key: str, api_key: str, language: str) -> None:
        super().__init__()
        self.language = language
        self.client = httpx.AsyncClient(
            headers={"project-key": project_key, "api-key": api_key}, timeout=TIMEOUT_S
        )

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame | None]:
        started = time.perf_counter()
        try:
            text = await asyncio.wait_for(self.transcribe(audio), TIMEOUT_S)
        except (
            httpx.HTTPError,
            TimeoutError,
            RuntimeError,
            KeyError,
        ) as error:  # logged; say it again
            yield ErrorFrame(error=f"chorus: {type(error).__name__}: {error}")
            return
        logger.info(f"chorus: {time.perf_counter() - started:.2f}s")
        if text:
            yield TranscriptionFrame(text, self._user_id, time_now_iso8601())

    async def transcribe(self, wav: bytes) -> str:
        upload = await self.client.post(
            URL,
            files={"file": ("speech.wav", wav, "audio/wav")},
            data={"task_type": "transcription", "language": self.language},
        )
        upload.raise_for_status()
        task_id = upload.json()["task_id"]
        while True:
            await asyncio.sleep(POLL_S)
            task = (await self.client.get(f"{URL}/{task_id}")).json()
            if task["status"] == "completed":
                return str(task["results"]["transcription"]["text"]).strip()
            if task["status"] == "failed":
                raise RuntimeError(task.get("error_message") or "failed")

    async def cleanup(self) -> None:
        await super().cleanup()
        await self.client.aclose()
