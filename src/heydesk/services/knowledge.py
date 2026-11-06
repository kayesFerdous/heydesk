"""The knowledge base: a business's documents, chunked, embedded and searched.

The text of each document is saved in SQL; its chunks and vectors go to Qdrant through the
`KnowledgeStore` port. Nothing is saved unless the whole document was embedded and stored, and
every call is scoped to one business (rule 6).
"""

import logging
import uuid
from collections.abc import Callable
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from heydesk.errors import KnowledgeUnavailable, NotFound, UnreadableDocument
from heydesk.models import Document
from heydesk.ports.embedder import Embedder, EmbeddingUnavailable
from heydesk.ports.knowledge_store import ChunkIn, KnowledgeStore, Snippet
from heydesk.runtime.text import chunk_text

log = logging.getLogger(__name__)

MAX_DOCUMENTS = 30  # per business
MAX_CHARS = 300_000  # per document: about 100 pages
MAX_CHUNKS = 400
SEARCH_LIMIT = 4


class KnowledgeService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        embedder: Embedder,
        store: KnowledgeStore,
        now: Callable[[], datetime],
    ) -> None:
        self.sessions = sessions
        self.embedder = embedder
        self.store = store
        self.now = now
        self._ready = False

    async def add(
        self,
        workspace_id: str,
        agent_id: str,
        title: str,
        text: str,
        kind: str = "text",
        filename: str = "",
    ) -> Document:
        """Chunks, embeds and stores the document, then saves it. Raises if any step fails,
        with nothing left behind."""
        text = text.strip()
        if not text:
            raise UnreadableDocument("That document is empty.")
        if len(text) > MAX_CHARS:
            raise UnreadableDocument(f"That's over {MAX_CHARS:,} characters. Split it in two.")
        chunks = chunk_text(text)
        if len(chunks) > MAX_CHUNKS:
            raise UnreadableDocument("That document is too long. Split it in two.")
        async with self.sessions() as s:
            have = await s.scalar(select(func.count()).where(Document.workspace_id == workspace_id))
        if (have or 0) >= MAX_DOCUMENTS:
            raise UnreadableDocument(
                f"A business can have {MAX_DOCUMENTS} documents. Delete one first."
            )

        document = Document(
            id=uuid.uuid4().hex,
            workspace_id=workspace_id,
            agent_id=agent_id,
            title=title.strip()[:200] or filename or "Untitled",
            filename=filename[:255],
            kind=kind,
            text=text,
            chunk_count=len(chunks),
            created_at=self.now(),
        )
        await self._index(workspace_id, document.id, document.title, chunks)
        try:
            async with self.sessions() as s, s.begin():
                s.add(document)
        except Exception:
            await self.store.delete_document(workspace_id, document.id)  # no half-saved document
            raise
        log.info("document added", extra={"document_id": document.id, "chunks": len(chunks)})
        return document

    async def all(self, workspace_id: str) -> list[Document]:
        """Newest first."""
        async with self.sessions() as s:
            rows = await s.scalars(
                select(Document)
                .where(Document.workspace_id == workspace_id)
                .order_by(Document.created_at.desc())
            )
            return list(rows)

    async def get(self, workspace_id: str, document_id: str) -> Document:
        async with self.sessions() as s:
            document = await s.scalar(
                select(Document).where(
                    Document.id == document_id, Document.workspace_id == workspace_id
                )
            )
        if document is None:
            raise NotFound("There's no such document.")
        return document

    async def delete(self, workspace_id: str, document_id: str) -> None:
        await self.get(workspace_id, document_id)  # 404 unless it's this business's
        try:
            await self._store_ready()
            await self.store.delete_document(workspace_id, document_id)
        except Exception as error:
            log.exception("deleting chunks failed", extra={"document_id": document_id})
            raise KnowledgeUnavailable("Couldn't remove it just now. Try again.") from error
        async with self.sessions() as s, s.begin():
            document = await s.get(Document, document_id)
            if document is not None:
                await s.delete(document)

    async def titles(self, agent_id: str) -> list[str]:
        async with self.sessions() as s:
            rows = await s.scalars(
                select(Document.title)
                .where(Document.agent_id == agent_id)
                .order_by(Document.created_at)
            )
            return list(rows)

    async def search(
        self, workspace_id: str, question: str, limit: int = SEARCH_LIMIT
    ) -> list[Snippet]:
        """The best pieces of the business's documents for a question. If the question can't be
        embedded, words alone decide: a turn never fails for that."""
        try:
            vector: list[float] | None = await self.embedder.embed_query(question)
        except EmbeddingUnavailable:
            log.warning("question not embedded; searching by words only")
            vector = None
        await self._store_ready()
        return await self.store.search(workspace_id, question, vector, limit)

    async def reindex(self) -> int:
        """Rebuilds every document's chunks in the store from the text saved here (after the
        collection or the embedding model changed). Returns the number of documents."""
        async with self.sessions() as s:
            documents = list(await s.scalars(select(Document)))
        for document in documents:
            await self.store.delete_document(document.workspace_id, document.id)
            await self._index(
                document.workspace_id, document.id, document.title, chunk_text(document.text)
            )
        return len(documents)

    async def _index(
        self, workspace_id: str, document_id: str, title: str, chunks: list[str]
    ) -> None:
        try:
            await self._store_ready()
            vectors = await self.embedder.embed_documents(chunks, title)
            await self.store.add(
                workspace_id,
                document_id,
                title,
                [
                    ChunkIn(n, text, v)
                    for n, (text, v) in enumerate(zip(chunks, vectors, strict=True))
                ],
            )
        except Exception as error:
            log.exception("indexing failed", extra={"document_id": document_id})
            await self._forget(workspace_id, document_id)
            raise KnowledgeUnavailable(
                "Couldn't read that document just now. Nothing was saved: try again."
            ) from error

    async def _forget(self, workspace_id: str, document_id: str) -> None:
        try:
            await self.store.delete_document(workspace_id, document_id)
        except Exception:
            log.exception("cleaning up chunks failed", extra={"document_id": document_id})

    async def _store_ready(self) -> None:
        """Makes the collection on first use, so the app starts even when Qdrant is away."""
        if not self._ready:
            await self.store.setup()
            self._ready = True
