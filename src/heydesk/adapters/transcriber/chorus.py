"""Speech in words with Gigalogy's Chorus: upload the file, then ask for the words until
they're ready. Inside, it is Groq's whisper-large-v3.

Slow: on 11 Nov every clip waited about 21 s in Chorus's queue and took about 1 s to hear, so
the words come about 23 s after the upload.
"""

import asyncio

import httpx

from heydesk.ports.transcriber import TranscriberUnavailable

URL = "https://api.maira.gigalogy.com/chorus/v1/speeches"
POLL_SECONDS = 0.5
TIMEOUT_SECONDS = 45.0  # the queue alone takes about 21 s


class ChorusTranscriber:
    def __init__(self, project_key: str, api_key: str, language: str) -> None:
        self.language = language  # "bn" or "en": VOICE_LANGUAGE
        self.client = httpx.AsyncClient(
            headers={"project-key": project_key, "api-key": api_key}, timeout=30.0
        )

    async def transcribe(self, audio: bytes, filename: str) -> str:
        try:
            async with asyncio.timeout(TIMEOUT_SECONDS):
                task_id = await self._upload(audio, filename)
                return await self._words(task_id)
        except (httpx.HTTPError, TimeoutError) as error:
            raise TranscriberUnavailable(f"chorus: {type(error).__name__}") from None

    async def _upload(self, audio: bytes, filename: str) -> str:
        res = await self.client.post(
            URL,
            files={"file": (filename, audio)},
            data={"task_type": "transcription", "language": self.language},
        )
        res.raise_for_status()
        return str(res.json()["task_id"])

    async def _words(self, task_id: str) -> str:
        """Asks until the task is done."""
        while True:
            await asyncio.sleep(POLL_SECONDS)
            res = await self.client.get(f"{URL}/{task_id}")
            res.raise_for_status()
            task = res.json()
            if task["status"] == "completed":
                return str(task["results"]["transcription"]["text"]).strip()
            if task["status"] == "failed":
                raise TranscriberUnavailable("chorus: it couldn't hear the audio")
