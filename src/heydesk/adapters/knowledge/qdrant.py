"""QdrantKnowledgeStore: hybrid search over a business's documents.

One collection holds every business's chunks, told apart by `workspace_id` in the payload (a
tenant index); every query filters by it. Each chunk has two vectors:

- `dense`: the embedding, compared by cosine: finds the same meaning in other words.
- `sparse`: the chunk's words, hashed, with Qdrant applying IDF: finds the exact word, price or
  name that a dense vector blurs. Words are cut the same way for both sides (`runtime.text`).

A query asks for both, and Qdrant fuses the two rankings (reciprocal rank fusion) in one call.
"""

import uuid
import zlib
from collections import Counter

from qdrant_client import AsyncQdrantClient, models

from heydesk.ports.knowledge_store import ChunkIn, Snippet
from heydesk.runtime.text import words

POINTS_PER_CALL = 64
CANDIDATES = 20  # what each of the two searches hands to the fusion
TF_SATURATION = 1.2  # BM25's k1: a word's 5th mention counts for little more than its 2nd


# Words that say nothing about a topic. Left in, a question like "poem about the moon" would
# "match" every passage that has a "the" (IDF only softens that). English, and the Bengali
# words that carry no topic; question words (when, কখন) stay out too: they're not in a document.
STOPWORDS = frozenset(
    [
        "a",
        "an",
        "the",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "am",
        "do",
        "does",
        "did",
        "have",
        "has",
        "had",
        "i",
        "me",
        "my",
        "we",
        "our",
        "you",
        "your",
        "it",
        "its",
        "this",
        "that",
        "these",
        "those",
        "of",
        "to",
        "in",
        "on",
        "at",
        "for",
        "from",
        "by",
        "with",
        "about",
        "as",
        "and",
        "or",
        "but",
        "not",
        "no",
        "so",
        "if",
        "then",
        "than",
        "too",
        "can",
        "could",
        "will",
        "would",
        "should",
        "may",
        "might",
        "what",
        "when",
        "where",
        "who",
        "whom",
        "which",
        "why",
        "how",
        "any",
        "some",
        "all",
        "more",
        "most",
        "very",
        "just",
        "also",
        "there",
        "here",
        "they",
        "them",
        "their",
        "he",
        "she",
        "him",
        "her",
        "his",
        "hers",
        "এবং",
        "ও",
        "বা",
        "কি",
        "কী",
        "কখন",
        "কোথায়",
        "কে",
        "কেন",
        "কীভাবে",
        "কত",
        "এই",
        "সেই",
        "এ",
        "যে",
        "যা",
        "না",
        "নয়",
        "আছে",
        "আমি",
        "আমার",
        "আপনি",
        "আপনার",
        "তুমি",
        "তোমার",
        "আমরা",
        "আমাদের",
        "তারা",
        "তাদের",
        "সে",
        "তার",
        "এটা",
        "ওটা",
        "কোন",
        "করে",
        "করা",
        "হয়",
        "হবে",
        "হলে",
        "থেকে",
        "জন্য",
        "সাথে",
    ]
)


def sparse_vector(text: str, *, query: bool = False) -> models.SparseVector:
    """A text as `{hashed word: weight}`. A chunk weighs a word by how often it appears
    (saturating); a query just says which words it has. Stop words are left out."""
    counts = Counter(zlib.crc32(w.encode()) for w in words(text) if w not in STOPWORDS)
    if query:
        return models.SparseVector(indices=list(counts), values=[1.0] * len(counts))
    values = [n * (TF_SATURATION + 1) / (n + TF_SATURATION) for n in counts.values()]
    return models.SparseVector(indices=list(counts), values=values)


class QdrantKnowledgeStore:
    def __init__(
        self,
        client: AsyncQdrantClient,
        collection: str,
        dimensions: int,
        dense_min_score: float = 0.0,
    ) -> None:
        self.client = client
        self.collection = collection
        self.dimensions = dimensions
        self.dense_min_score = dense_min_score  # a cosine below this isn't "similar" at all

    async def setup(self) -> None:
        if await self.client.collection_exists(self.collection):
            return
        await self.client.create_collection(
            self.collection,
            vectors_config={
                "dense": models.VectorParams(size=self.dimensions, distance=models.Distance.COSINE)
            },
            sparse_vectors_config={
                "sparse": models.SparseVectorParams(modifier=models.Modifier.IDF)
            },
        )
        await self.client.create_payload_index(
            self.collection,
            "workspace_id",
            models.KeywordIndexParams(type=models.KeywordIndexType.KEYWORD, is_tenant=True),
        )
        await self.client.create_payload_index(
            self.collection, "document_id", models.PayloadSchemaType.KEYWORD
        )

    async def add(self, ws: str, document_id: str, title: str, chunks: list[ChunkIn]) -> None:
        points = [
            models.PointStruct(
                id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{document_id}:{c.position}")),
                vector={"dense": c.vector, "sparse": sparse_vector(c.text)},
                payload={
                    "workspace_id": ws,
                    "document_id": document_id,
                    "title": title,
                    "position": c.position,
                    "text": c.text,
                },
            )
            for c in chunks
        ]
        for start in range(0, len(points), POINTS_PER_CALL):
            await self.client.upsert(self.collection, points[start : start + POINTS_PER_CALL])

    async def delete_document(self, ws: str, document_id: str) -> None:
        await self.client.delete(
            self.collection,
            models.FilterSelector(
                filter=models.Filter(
                    must=[
                        _field_equals("workspace_id", ws),
                        _field_equals("document_id", document_id),
                    ]
                )
            ),
        )

    async def search(
        self, ws: str, question: str, vector: list[float] | None, limit: int
    ) -> list[Snippet]:
        mine = models.Filter(must=[_field_equals("workspace_id", ws)])
        legs: list[models.Prefetch] = []
        if vector is not None:
            legs.append(
                models.Prefetch(
                    query=vector,
                    using="dense",
                    limit=CANDIDATES,
                    filter=mine,
                    score_threshold=self.dense_min_score or None,
                )
            )
        asked = sparse_vector(question, query=True)
        if asked.indices:
            legs.append(models.Prefetch(query=asked, using="sparse", limit=CANDIDATES, filter=mine))
        if not legs:
            return []
        found = await self.client.query_points(
            self.collection,
            prefetch=legs,
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            query_filter=mine,
            limit=limit,
            with_payload=True,
        )
        return [
            Snippet(p.payload["document_id"], p.payload["title"], p.payload["text"])
            for p in found.points
            if p.payload
        ]


def _field_equals(key: str, value: str) -> models.FieldCondition:
    return models.FieldCondition(key=key, match=models.MatchValue(value=value))
