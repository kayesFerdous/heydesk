"""Text-to-speech in Bangla: each sentence goes to our local TTS server (D84, `tts_server/`).

The server answers with the whole sentence as raw 16-bit PCM, which Pipecat resamples for the
call. A sentence takes 0.6–1.3 s on an M2's CPU, and speech starts after the first one.
"""

from collections.abc import AsyncGenerator, AsyncIterator

import httpx
from pipecat.frames.frames import ErrorFrame, Frame
from pipecat.services.tts_service import TTSService
from pipecat.utils.text.base_text_filter import BaseTextFilter

TIMEOUT_S = 15.0


class BanglaTTS(TTSService):
    def __init__(self, url: str, text_filters: list[BaseTextFilter]) -> None:
        super().__init__(push_start_frame=True, push_stop_frames=True, text_filters=text_filters)
        self.client = httpx.AsyncClient(base_url=url, timeout=TIMEOUT_S)

    def can_generate_metrics(self) -> bool:
        return True

    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame]:
        try:
            response = await self.client.post("/speak", json={"text": text})
            response.raise_for_status()
        except httpx.HTTPError as error:  # logged; this sentence isn't heard
            yield ErrorFrame(error=f"bangla tts: {type(error).__name__}: {error}")
            return
        await self.start_tts_usage_metrics(text)

        async def audio() -> AsyncIterator[bytes]:
            yield response.content

        rate = int(response.headers["X-Sample-Rate"])
        async for frame in self._stream_audio_frames_from_iterator(
            audio(), in_sample_rate=rate, context_id=context_id
        ):
            await self.stop_ttfb_metrics()
            yield frame

    async def cleanup(self) -> None:
        await super().cleanup()
        await self.client.aclose()
