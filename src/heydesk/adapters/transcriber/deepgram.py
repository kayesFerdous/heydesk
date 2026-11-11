"""Speech in words with Deepgram: the whole file in one request.

The same model a call streams to (nova-3-general), told the server's language, so Bangla comes
back in Bangla letters. Deepgram reads the chat apps' ogg and mp4 as they are.
"""

import httpx

from heydesk.ports.transcriber import TranscriberUnavailable

URL = "https://api.deepgram.com/v1/listen"
MODEL = "nova-3-general"


class DeepgramTranscriber:
    def __init__(self, api_key: str, language: str, timeout: float = 30.0) -> None:
        self.language = language  # "bn" or "en": VOICE_LANGUAGE
        self.client = httpx.AsyncClient(
            headers={"Authorization": f"Token {api_key}"}, timeout=timeout
        )

    async def transcribe(self, audio: bytes, filename: str) -> str:
        try:
            res = await self.client.post(
                URL,
                params={"model": MODEL, "language": self.language, "punctuate": "true"},
                headers={"Content-Type": "audio/*"},  # Deepgram finds the kind itself
                content=audio,
            )
            res.raise_for_status()
        except httpx.HTTPError as error:
            raise TranscriberUnavailable(f"deepgram: {type(error).__name__}") from None
        words = res.json()["results"]["channels"][0]["alternatives"][0]["transcript"]
        return str(words).strip()
