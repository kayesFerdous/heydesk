"""The `Embedder` port: text → vectors, for searching a business's documents (D33)."""

from typing import Protocol


class EmbeddingUnavailable(Exception):
    """The embedding service didn't answer (down, slow, over quota, a bad key)."""


class Embedder(Protocol):
    dimensions: int

    async def embed_documents(self, texts: list[str], title: str = "") -> list[list[float]]:
        """One vector per text, for texts from the document called `title`."""
        ...

    async def embed_query(self, text: str) -> list[float]:
        """A visitor's question, as a vector comparable with the documents'."""
        ...
