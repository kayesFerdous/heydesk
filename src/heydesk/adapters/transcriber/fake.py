"""Offline: every voice note says `text` (tests and `LLM_PROVIDER=fake`)."""


class FakeTranscriber:
    def __init__(self, text: str = "Do you have the black kurti in M?") -> None:
        self.text = text
        self.heard: list[str] = []  # the file names it was given

    async def transcribe(self, audio: bytes, filename: str) -> str:
        self.heard.append(filename)
        return self.text
