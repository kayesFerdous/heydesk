"""Voice notes in words with Groq's Whisper. It reads Telegram's and WhatsApp's ogg and
Messenger's mp4 as they come, and finds the language itself: Bangla, English or both."""

from groq import AsyncGroq, GroqError

from heydesk.ports.transcriber import TranscriberUnavailable

MODEL = "whisper-large-v3-turbo"


class GroqTranscriber:
    def __init__(self, api_key: str, timeout: float = 30.0) -> None:
        self.client = AsyncGroq(api_key=api_key, timeout=timeout, max_retries=1)

    async def transcribe(self, audio: bytes, filename: str) -> str:
        try:
            said = await self.client.audio.transcriptions.create(
                file=(filename, audio), model=MODEL
            )
        except GroqError as error:
            raise TranscriberUnavailable(f"groq: {type(error).__name__}") from None
        return said.text.strip()
