"""FakeEmbedder: offline vectors for tests and for running without a key.

A bag of words hashed into a few buckets: texts that share words are close, which is enough to
test search. It knows nothing about meaning, so a real run needs the Gemini one.
"""

import math
import zlib

from heydesk.runtime.text import words


class FakeEmbedder:
    def __init__(self, dimensions: int = 64) -> None:
        self.dimensions = dimensions
        self.fail = False  # a test sets it to see search without the embedder

    async def embed_documents(self, texts: list[str], title: str = "") -> list[list[float]]:
        return [self._vector(t) for t in texts]

    async def embed_query(self, text: str) -> list[float]:
        if self.fail:
            from heydesk.ports.embedder import EmbeddingUnavailable

            raise EmbeddingUnavailable("the fake embedder was told to fail")
        return self._vector(text)

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for word in words(text):
            vector[zlib.crc32(word.encode()) % self.dimensions] += 1.0
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]
