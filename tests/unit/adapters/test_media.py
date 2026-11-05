from datetime import UTC, datetime
from pathlib import Path

import pytest

from heydesk.adapters.embedder.fake import FakeEmbedder
from heydesk.adapters.media import (
    DiskMedia,
    canonical_query,
    check_public_url,
    drive_download_link,
    signed_headers,
)
from heydesk.ports.media import ImageUnreachable, MediaUnavailable
from heydesk.services.photos import PhotoService

AWS_KEY = ("AKIAIOSFODNN7EXAMPLE", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY")
AWS_DAY = datetime(2013, 5, 24, tzinfo=UTC)


def signature(headers: dict[str, str]) -> str:
    return headers["Authorization"].split("Signature=")[1]


def test_signing_matches_amazons_own_examples() -> None:
    """Amazon's S3 Signature Version 4 examples: a GET of an object, and a bucket listing."""
    host = "examplebucket.s3.amazonaws.com"
    get = signed_headers(
        "GET", host, "/test.txt", "", {"Range": "bytes=0-9"}, b"", *AWS_KEY, AWS_DAY, "us-east-1"
    )
    assert signature(get) == "f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41"
    query = canonical_query({"prefix": "J", "max-keys": "2"})
    listing = signed_headers("GET", host, "/", query, {}, b"", *AWS_KEY, AWS_DAY, "us-east-1")
    assert signature(listing) == "34b48302e7b5fa45bde8084f4b7868a86f0a534bc59db6670ed5711ef69dc6f7"


async def test_the_disk_keeps_reads_lists_and_deletes(tmp_path: Path) -> None:
    disk = DiskMedia(str(tmp_path))
    await disk.put("ws1/products/a.jpg", b"one")
    await disk.put("ws1/chat/b.jpg", b"two")

    assert await disk.get("ws1/products/a.jpg") == b"one"
    assert await disk.keys() == ["ws1/chat/b.jpg", "ws1/products/a.jpg"]
    assert await disk.keys("ws1/products/") == ["ws1/products/a.jpg"]
    await disk.delete("ws1/products/a.jpg")
    await disk.delete("ws1/products/a.jpg")  # gone already: fine
    with pytest.raises(MediaUnavailable):
        await disk.get("ws1/products/a.jpg")


def test_a_product_photo_is_only_one_of_the_business_s_own(tmp_path: Path) -> None:
    photos = PhotoService(FakeEmbedder(), None, DiskMedia(str(tmp_path)), 0.75, None)  # type: ignore[arg-type]
    name = "0123456789abcdef0123456789abcdef.jpg"

    assert photos.is_product_photo("ws1", f"/media/ws1/products/{name}")
    assert not photos.is_product_photo("ws2", f"/media/ws1/products/{name}")
    assert not photos.is_product_photo("ws1", f"/media/ws1/products/../../ws2/products/{name}")
    assert not photos.is_product_photo("ws1", f"https://elsewhere.com/ws1/products/{name}")


async def test_a_photo_link_must_be_a_public_address() -> None:
    """A business's list names any address: never this server or its network."""
    for url in ("http://127.0.0.1/x.jpg", "http://10.0.0.5/x.jpg", "file:///etc/passwd"):
        with pytest.raises(ImageUnreachable):
            await check_public_url(url, local=False)
    await check_public_url("http://localhost:8088/x.jpg", local=True)  # development's test shop
    assert drive_download_link("https://drive.google.com/file/d/1AbC-d_9/view?usp=sharing") == (
        "https://drive.google.com/uc?export=download&id=1AbC-d_9"
    )
