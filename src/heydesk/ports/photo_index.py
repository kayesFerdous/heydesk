"""The `PhotoIndex` port: where a business's product photos are searched by likeness.

A photo is known by its URL (`/media/<workspace_id>/products/<id>.jpg`). Every call carries the
workspace: an index may never answer across businesses (rule 6).
"""

from typing import Protocol


class PhotoIndex(Protocol):
    async def setup(self) -> None:
        """Makes the collection and its index if they aren't there. Safe to call every time."""
        ...

    async def add(self, ws: str, url: str, vector: list[float]) -> None: ...

    async def nearest(
        self, ws: str, vector: list[float], limit: int, min_score: float
    ) -> list[tuple[str, float]]:
        """This business's photos most like `vector`, at or above `min_score`, best first:
        (url, score)."""
        ...
