"""The `KnowledgeStore` port: where a business's document chunks are searched.

Every call carries the workspace: a store may never answer across businesses (rule 6).
"""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ChunkIn:
    """One piece of a document, ready to store: its text and its vector."""

    position: int
    text: str
    vector: list[float]


@dataclass(frozen=True)
class Snippet:
    """A piece of a document that fits a question."""

    document_id: str
    title: str
    text: str


class KnowledgeStore(Protocol):
    async def setup(self) -> None:
        """Makes the collection and its indexes if they aren't there. Safe to call every start."""
        ...

    async def add(
        self, workspace_id: str, document_id: str, title: str, chunks: list[ChunkIn]
    ) -> None: ...

    async def delete_document(self, workspace_id: str, document_id: str) -> None: ...

    async def search(
        self, workspace_id: str, question: str, vector: list[float] | None, limit: int
    ) -> list[Snippet]:
        """The best `limit` pieces for a question, by meaning (`vector`) and by its words.
        With no vector (the embedder is down), by words alone."""
        ...
