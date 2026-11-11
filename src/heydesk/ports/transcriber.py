"""The `Transcriber` port: speech in words. A visitor's voice note, and on Chorus, each thing a
caller says."""

from typing import Protocol


class TranscriberUnavailable(Exception):
    """The audio couldn't be turned into words: the service didn't answer or refused it."""


class Transcriber(Protocol):
    async def transcribe(self, audio: bytes, filename: str) -> str:
        """The words in `audio`, a whole file: a voice note as the chat app sent it
        ("voice.ogg", "voice.mp4"), or a call's "speech.wav"; "" when nothing was said.
        Raises TranscriberUnavailable."""
        ...
