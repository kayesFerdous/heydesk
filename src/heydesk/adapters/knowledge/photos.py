"""QdrantPhotoIndex: a shop's product photos, searched by likeness.

One collection for every business, told apart by `workspace_id` in the payload (a tenant index),
like the documents' chunks. One dense vector per photo, compared by cosine; no words to match.
"""

import uuid

from qdrant_client import AsyncQdrantClient, models


class QdrantPhotoIndex:
    def __init__(self, client: AsyncQdrantClient, collection: str, dimensions: int) -> None:
        self.client = client
        self.collection = collection
        self.dimensions = dimensions

    async def setup(self) -> None:
        if await self.client.collection_exists(self.collection):
            return
        await self.client.create_collection(
            self.collection,
            vectors_config=models.VectorParams(
                size=self.dimensions, distance=models.Distance.COSINE
            ),
        )
        await self.client.create_payload_index(
            self.collection,
            "workspace_id",
            models.KeywordIndexParams(type=models.KeywordIndexType.KEYWORD, is_tenant=True),
        )

    async def add(self, ws: str, url: str, vector: list[float]) -> None:
        point = models.PointStruct(
            id=str(uuid.uuid5(uuid.NAMESPACE_URL, url)),  # the same photo again replaces it
            vector=vector,
            payload={"workspace_id": ws, "url": url},
        )
        await self.client.upsert(self.collection, [point])

    async def nearest(
        self, ws: str, vector: list[float], limit: int, min_score: float
    ) -> list[tuple[str, float]]:
        mine = models.FieldCondition(key="workspace_id", match=models.MatchValue(value=ws))
        found = await self.client.query_points(
            self.collection,
            query=vector,
            query_filter=models.Filter(must=[mine]),
            limit=limit,
            score_threshold=min_score,
            with_payload=True,
        )
        return [(p.payload["url"], p.score) for p in found.points if p.payload]
