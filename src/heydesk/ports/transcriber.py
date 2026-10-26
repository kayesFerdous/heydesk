"""The `Transcriber` port: a visitor's voice note in words (G2, D147)."""

from typing import Protocol


class TranscriberUnavailable(Exception):
    """The audio couldn't be turned into words: the service didn't answer or refused it."""


class Transcriber(Protocol):
    async def transcribe(self, audio: bytes, filename: str) -> str:
        """The words in `audio`, a whole file as the chat app sent it ("voice.ogg",
        "voice.mp4": its kind is read from the name); "" when nothing was said. Raises
        TranscriberUnavailable."""
        ...
