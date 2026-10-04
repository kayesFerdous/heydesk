"""GeminiEmbedder: Google's `gemini-embedding-2` behind the `Embedder` port (D33).

Over httpx, like the other adapters. For text, this model takes a task written into the input
rather than a parameter: documents as `title: … | text: …`, questions as `task: search result
| query: …`. Vectors are cut to `dimensions` (768), which Qdrant compares by cosine.
"""

import httpx

from heydesk.ports.embedder import EmbeddingUnavailable

BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"
BATCH = 50  # texts per request


class GeminiEmbedder:
    def __init__(
        self,
        api_key: str,
        model: str = "gemini-embedding-2",
        dimensions: int = 768,
        timeout: float = 20.0,
    ) -> None:
        self.client = httpx.AsyncClient(timeout=timeout)
        self.headers = {"x-goog-api-key": api_key}
        self.model = model
        self.dimensions = dimensions

    async def embed_documents(self, texts: list[str], title: str = "") -> list[list[float]]:
        prefix = f"title: {title.strip() or 'none'} | text: "
        vectors: list[list[float]] = []
        for start in range(0, len(texts), BATCH):
            vectors += await self._embed([prefix + t for t in texts[start : start + BATCH]])
        return vectors

    async def embed_query(self, text: str) -> list[float]:
        return (await self._embed([f"task: search result | query: {text}"]))[0]

    async def _embed(self, inputs: list[str]) -> list[list[float]]:
        body = {
            "requests": [
                {
                    "model": f"models/{self.model}",
                    "content": {"parts": [{"text": text}]},
                    "outputDimensionality": self.dimensions,
                }
                for text in inputs
            ]
        }
        try:
            response = await self.client.post(
                f"{BASE_URL}/{self.model}:batchEmbedContents", headers=self.headers, json=body
            )
            response.raise_for_status()
            vectors = [e["values"] for e in response.json()["embeddings"]]
        except (httpx.HTTPError, KeyError, ValueError) as error:
            # Never put the response in the message: it can echo what we sent (rule: no text in logs)
            raise EmbeddingUnavailable(
                f"Gemini embeddings failed: {type(error).__name__}"
            ) from error
        if len(vectors) != len(inputs):
            raise EmbeddingUnavailable("Gemini embeddings returned the wrong number of vectors")
        return vectors
