"""The `MediaStore` port: where photo files are kept. The server's disk, or Cloudflare R2.

A file is known by its key, `<workspace_id>/<folder>/<id>.jpg`. Browsers fetch it at the
store's `url` + "/" + key: `/media/...` from this server, or the bucket's public address.
"""

from collections.abc import Awaitable, Callable
from typing import Protocol


class MediaUnavailable(Exception):
    """The file couldn't be written or read: the bucket didn't answer, or it isn't there."""


class ImageUnreachable(Exception):
    """A photo's link couldn't be downloaded: a bad address, a private one, too big, or gone."""


type FetchImage = Callable[[str], Awaitable[bytes]]  # a photo's link → its bytes


class MediaStore(Protocol):
    url: str  # where browsers fetch the files, with no "/" at the end

    async def put(self, key: str, data: bytes) -> None: ...

    async def get(self, key: str) -> bytes: ...

    async def exists(self, key: str) -> bool: ...

    async def delete(self, key: str) -> None:
        """Gone, or never there: both are fine."""
        ...

    async def keys(self, prefix: str = "") -> list[str]:
        """Every file's key that starts with `prefix`."""
        ...
