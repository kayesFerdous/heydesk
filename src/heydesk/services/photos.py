"""Photos (D57): a shop's product photos, and the ones visitors send to ask "do you have this?".

Every photo is saved as a JPEG of at most 1024 px under the business's folder in `MEDIA_DIR`,
served at `/media/...`. A product photo is also embedded (Gemini) into the photo index; a
visitor's is embedded only to search it.

    media/<workspace_id>/products/<id>.jpg   a product's, in the index
    media/<workspace_id>/chat/<id>.jpg       a visitor's, kept for the owner's view
"""

import asyncio
import io
import logging
import uuid
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from heydesk.errors import KnowledgeUnavailable, UnreadablePhoto
from heydesk.ports.embedder import Embedder
from heydesk.ports.photo_index import PhotoIndex

log = logging.getLogger(__name__)

LARGEST_SIDE = 1024  # px: enough to tell one shirt from another, small to send and keep
MAX_UPLOAD = 10_000_000  # bytes: a phone photo, before it's made smaller
NEAREST = 10  # photos asked for; a product with several photos shows up once (`photo_line`)


@dataclass(frozen=True)
class Photo:
    url: str  # "/media/<workspace_id>/products/<id>.jpg": what's saved and shown
    jpeg: bytes


class PhotoService:
    def __init__(
        self, embedder: Embedder, index: PhotoIndex, media_dir: str, min_similarity: float
    ) -> None:
        self.embedder = embedder
        self.index = index
        self.media = Path(media_dir)
        self.min_similarity = min_similarity
        self._ready = False

    async def add_product_photo(self, ws: str, data: bytes) -> Photo:
        """Saves it and puts it in the index. If it can't be embedded, nothing is kept."""
        photo = await self.save(ws, "products", data)
        try:
            await self._index_ready()
            await self.index.add(ws, photo.url, await self.embedder.embed_image(photo.jpeg))
        except Exception as error:  # Gemini or Qdrant: the owner tries again
            self.path(photo.url).unlink(missing_ok=True)
            log.warning("photo not indexed", extra={"workspace_id": ws, "error": repr(error)})
            raise KnowledgeUnavailable(
                "The photo couldn't be added just now. Please try again."
            ) from error
        return photo

    async def save(self, ws: str, folder: str, data: bytes) -> Photo:
        """A JPEG of at most `LARGEST_SIDE`, written to the business's `folder`."""
        if len(data) > MAX_UPLOAD:
            raise UnreadablePhoto("A photo can be up to 10 MB.")
        jpeg = await asyncio.to_thread(to_jpeg, data)
        url = f"/media/{ws}/{folder}/{uuid.uuid4().hex}.jpg"
        path = self.path(url)
        path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_bytes, jpeg)
        return Photo(url, jpeg)

    async def nearest(self, ws: str, jpeg: bytes) -> list[tuple[str, float]] | None:
        """The business's product photos most like this one, best first: (url, score). None
        when Gemini or Qdrant didn't answer: the agent then asks what it is."""
        try:
            await self._index_ready()
            vector = await self.embedder.embed_image(jpeg)
            return await self.index.nearest(ws, vector, NEAREST, self.min_similarity)
        except Exception:  # Gemini or Qdrant didn't answer
            log.exception("photo not searched", extra={"workspace_id": ws})
            return None

    def is_products(self, ws: str, url: str) -> bool:
        """`url` is one of this business's product photos, and its file is there."""
        return url.startswith(f"/media/{ws}/products/") and self.path(url).is_file()

    async def reindex(self) -> int:
        """Embeds every product photo again from its file (a new collection or model)."""
        await self._index_ready()
        count = 0
        for path in sorted(self.media.glob("*/products/*.jpg")):
            ws = path.parts[-3]
            url = f"/media/{ws}/products/{path.name}"
            await self.index.add(ws, url, await self.embedder.embed_image(path.read_bytes()))
            count += 1
        return count

    async def read(self, url: str) -> bytes:
        """A saved photo's JPEG, to upload where a URL can't be fetched (Telegram, D63)."""
        return await asyncio.to_thread(self.path(url).read_bytes)

    def path(self, url: str) -> Path:
        return self.media / url.removeprefix("/media/")

    async def _index_ready(self) -> None:
        if not self._ready:
            await self.index.setup()
            self._ready = True


def to_jpeg(data: bytes) -> bytes:
    """Any picture Pillow reads (JPEG, PNG, WebP…) as a JPEG of at most `LARGEST_SIDE`. Gemini
    takes only JPEG and PNG, so every photo is kept as a JPEG."""
    try:
        image = Image.open(io.BytesIO(data))
        image.thumbnail((LARGEST_SIDE, LARGEST_SIDE))
        out = io.BytesIO()
        image.convert("RGB").save(out, "JPEG", quality=85)
    except (UnidentifiedImageError, OSError, ValueError) as error:
        raise UnreadablePhoto("That file isn't a photo we can read. Try a JPEG or PNG.") from error
    return out.getvalue()
