"""FakeEmbedder: offline vectors for tests and for running without a key.

A bag of words hashed into a few buckets: texts that share words are close, which is enough to
test search. It knows nothing about meaning, so a real run needs the Gemini one. A photo gets a
vector from its bytes: the same photo matches itself, and any other is far from it.
"""

import hashlib
import math
import random
import zlib

from heydesk.ports.embedder import EmbeddingUnavailable
from heydesk.runtime.text import words


class FakeEmbedder:
    def __init__(self, dimensions: int = 64) -> None:
        self.dimensions = dimensions
        self.fail_documents = False  # a test sets these to see the knowledge base without it
        self.fail_queries = False

    async def embed_documents(self, texts: list[str], title: str = "") -> list[list[float]]:
        if self.fail_documents:
            raise EmbeddingUnavailable("the fake embedder was told to fail")
        return [self._vector(t) for t in texts]

    async def embed_query(self, text: str) -> list[float]:
        if self.fail_queries:
            raise EmbeddingUnavailable("the fake embedder was told to fail")
        return self._vector(text)

    async def embed_image(self, jpeg: bytes) -> list[float]:
        if self.fail_queries:
            raise EmbeddingUnavailable("the fake embedder was told to fail")
        rng = random.Random(hashlib.sha256(jpeg).digest())
        vector = [rng.gauss(0, 1) for _ in range(self.dimensions)]
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for word in words(text):
            vector[zlib.crc32(word.encode()) % self.dimensions] += 1.0
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]
