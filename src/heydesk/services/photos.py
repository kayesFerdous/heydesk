"""Photos (D57): a shop's product photos, and the ones visitors send to ask "do you have this?".

Every photo is saved as a JPEG of at most 1024 px under the business's folder in the media
store: this server's disk, or Cloudflare R2 (D101). A product photo is also embedded (Gemini)
into the photo index; a visitor's is embedded only to search it.

    <workspace_id>/products/<id>.jpg   a product's, in the index
    <workspace_id>/chat/<id>.jpg       a visitor's, kept for the owner's view
    <workspace_id>/logo/<id>.jpg       the business's logo, for the website chat (D82)
"""

import asyncio
import io
import logging
import re
import uuid
from dataclasses import dataclass

from PIL import Image, UnidentifiedImageError

from heydesk.errors import KnowledgeUnavailable, UnreadablePhoto
from heydesk.ports.embedder import Embedder
from heydesk.ports.media import MediaStore, MediaUnavailable
from heydesk.ports.photo_index import PhotoIndex

log = logging.getLogger(__name__)

LARGEST_SIDE = 1024  # px: enough to tell one shirt from another, small to send and keep
MAX_UPLOAD = 10_000_000  # bytes: a phone photo, before it's made smaller
NEAREST = 10  # photos asked for; a product with several photos shows up once (`photo_line`)


@dataclass(frozen=True)
class Photo:
    url: str  # the media store's url + "/<workspace_id>/products/<id>.jpg": saved and shown
    jpeg: bytes


class PhotoService:
    def __init__(
        self, embedder: Embedder, index: PhotoIndex, media: MediaStore, min_similarity: float
    ) -> None:
        self.embedder = embedder
        self.index = index
        self.media = media
        self.min_similarity = min_similarity
        self._ready = False

    async def add_product_photo(self, ws: str, data: bytes) -> Photo:
        """Saves it and puts it in the index. If it can't be embedded, nothing is kept."""
        photo = await self.save(ws, "products", data)
        try:
            await self._index_ready()
            await self.index.add(ws, photo.url, await self.embedder.embed_image(photo.jpeg))
        except Exception as error:  # Gemini or Qdrant: the owner tries again
            await self.media.delete(self.key(photo.url))
            log.warning("photo not indexed", extra={"workspace_id": ws, "error": repr(error)})
            raise KnowledgeUnavailable(
                "The photo couldn't be added just now. Please try again."
            ) from error
        return photo

    async def save(self, ws: str, folder: str, data: bytes, largest: int = LARGEST_SIDE) -> Photo:
        """A JPEG of at most `largest` px, written to the business's `folder`."""
        if len(data) > MAX_UPLOAD:
            raise UnreadablePhoto("A photo can be up to 10 MB.")
        jpeg = await asyncio.to_thread(to_jpeg, data, largest)
        key = f"{ws}/{folder}/{uuid.uuid4().hex}.jpg"
        try:
            await self.media.put(key, jpeg)
        except MediaUnavailable as error:
            log.warning("photo not stored", extra={"workspace_id": ws, "error": repr(error)})
            raise KnowledgeUnavailable(
                "The photo couldn't be saved just now. Please try again."
            ) from error
        return Photo(f"{self.media.url}/{key}", jpeg)

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
        """`url` is the address of one of this business's product photos: its folder, and a
        name we gave (so no "../" reaches another business's)."""
        folder = re.escape(f"{self.media.url}/{ws}/products/")
        return re.fullmatch(folder + r"[0-9a-f]{32}\.jpg", url) is not None

    async def reindex(self) -> int:
        """Embeds every product photo again from its file (a new collection or model)."""
        await self._index_ready()
        count = 0
        for key in await self.media.keys():
            ws, folder, _ = (key.split("/") + ["", ""])[:3]
            if folder != "products":
                continue
            jpeg = await self.media.get(key)
            await self.index.add(
                ws, f"{self.media.url}/{key}", await self.embedder.embed_image(jpeg)
            )
            count += 1
        return count

    async def read(self, url: str) -> bytes:
        """A saved photo's JPEG, to upload where a URL can't be fetched (Telegram, D63).
        Raises MediaUnavailable when it can't be read."""
        return await self.media.get(self.key(url))

    def key(self, url: str) -> str:
        return url.removeprefix(f"{self.media.url}/")

    async def _index_ready(self) -> None:
        if not self._ready:
            await self.index.setup()
            self._ready = True


def to_jpeg(data: bytes, largest: int = LARGEST_SIDE) -> bytes:
    """Any picture Pillow reads (JPEG, PNG, WebP…) as a JPEG of at most `largest` px. Gemini
    takes only JPEG and PNG, so every photo is kept as a JPEG."""
    try:
        image = Image.open(io.BytesIO(data))
        image.thumbnail((largest, largest))
        flat = Image.new(
            "RGB", image.size, "white"
        )  # a transparent PNG (a logo) on white, not black
        flat.paste(image.convert("RGBA"), mask=image.convert("RGBA"))
        out = io.BytesIO()
        flat.save(out, "JPEG", quality=85)
    except (UnidentifiedImageError, OSError, ValueError) as error:
        raise UnreadablePhoto("That file isn't a photo we can read. Try a JPEG or PNG.") from error
    return out.getvalue()
