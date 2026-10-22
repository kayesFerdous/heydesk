"""Photo files on this server's disk, or in a Cloudflare R2 bucket (D101).

R2 speaks Amazon S3's API. Each request is signed with AWS Signature Version 4 (`signed`),
which is a few lines of hashlib, so there's no SDK to install.
"""

import asyncio
import hashlib
import hmac
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote
from xml.etree import ElementTree

import httpx

from heydesk.ports.media import MediaUnavailable

S3 = "{http://s3.amazonaws.com/doc/2006-03-01/}"  # the XML namespace of a bucket's listing


class DiskMedia:
    """Under `MEDIA_DIR`, served by this app at `/media` (main.py)."""

    url = "/media"

    def __init__(self, folder: str) -> None:
        self.folder = Path(folder)

    async def put(self, key: str, data: bytes) -> None:
        path = self.folder / key
        path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_bytes, data)

    async def get(self, key: str) -> bytes:
        try:
            return await asyncio.to_thread((self.folder / key).read_bytes)
        except OSError as error:
            raise MediaUnavailable(f"no file {key}") from error

    async def delete(self, key: str) -> None:
        (self.folder / key).unlink(missing_ok=True)

    async def keys(self, prefix: str = "") -> list[str]:
        found = (p.relative_to(self.folder).as_posix() for p in self.folder.rglob("*"))
        return sorted(k for k in found if k.startswith(prefix) and (self.folder / k).is_file())


class R2Media:
    """One bucket, public at `public_url` (its r2.dev address or the shop's own domain)."""

    def __init__(
        self,
        account_id: str,
        key_id: str,
        secret: str,
        bucket: str,
        public_url: str,
        timeout: float = 20.0,
    ) -> None:
        self.endpoint = f"https://{account_id}.r2.cloudflarestorage.com"
        self.key_id, self.secret, self.bucket = key_id, secret, bucket
        self.url = public_url.rstrip("/")
        self.client = httpx.AsyncClient(timeout=timeout)

    async def put(self, key: str, data: bytes) -> None:
        await self._call("PUT", key, data, {"content-type": "image/jpeg"})

    async def get(self, key: str) -> bytes:
        return (await self._call("GET", key)).content

    async def delete(self, key: str) -> None:
        await self._call("DELETE", key)

    async def keys(self, prefix: str = "") -> list[str]:
        found: list[str] = []
        query = {"list-type": "2", "prefix": prefix}
        while True:  # a listing is at most 1000 keys; the next part starts at its token
            listing = ElementTree.fromstring((await self._call("GET", "", query=query)).content)
            found += [e.text or "" for e in listing.iter(f"{S3}Key")]
            token = listing.findtext(f"{S3}NextContinuationToken")
            if not token:
                return found
            query["continuation-token"] = token

    async def _call(
        self,
        method: str,
        key: str,
        body: bytes = b"",
        headers: dict[str, str] | None = None,
        query: dict[str, str] | None = None,
    ) -> httpx.Response:
        path = quote(f"/{self.bucket}/{key}" if key else f"/{self.bucket}", safe="/-_.~")
        query_text = canonical_query(query or {})
        signing = signed(
            method,
            self.endpoint.removeprefix("https://"),
            path,
            query_text,
            {},
            body,
            self.key_id,
            self.secret,
            datetime.now(UTC),
        )
        url = f"{self.endpoint}{path}" + (f"?{query_text}" if query_text else "")
        try:
            response = await self.client.request(
                method, url, content=body, headers={**(headers or {}), **signing}
            )
        except httpx.HTTPError as error:
            raise MediaUnavailable(f"R2 didn't answer: {error!r}") from error
        if response.status_code >= 300:
            raise MediaUnavailable(f"R2 said {response.status_code} to {method} {key}")
        return response


def canonical_query(query: dict[str, str]) -> str:
    return "&".join(
        f"{quote(k, safe='-_.~')}={quote(v, safe='-_.~')}" for k, v in sorted(query.items())
    )


def signed(
    method: str,
    host: str,
    path: str,
    query: str,
    headers: dict[str, str],
    body: bytes,
    key_id: str,
    secret: str,
    now: datetime,
    region: str = "auto",  # R2's; Amazon's own buckets name theirs
) -> dict[str, str]:
    """The headers that sign a request (AWS Signature Version 4, service "s3"): `headers` with
    `x-amz-date`, `x-amz-content-sha256` and `Authorization` added. `path` and `query` are
    already in their canonical (encoded, sorted) form."""
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    day = stamp[:8]
    payload = hashlib.sha256(body).hexdigest()
    all_headers = {
        "host": host,
        **{k.lower(): v.strip() for k, v in headers.items()},
        "x-amz-content-sha256": payload,
        "x-amz-date": stamp,
    }
    names = sorted(all_headers)
    request = "\n".join(
        [
            method,
            path,
            query,
            "".join(f"{n}:{all_headers[n]}\n" for n in names),
            ";".join(names),
            payload,
        ]
    )
    scope = f"{day}/{region}/s3/aws4_request"
    to_sign = "\n".join(
        ["AWS4-HMAC-SHA256", stamp, scope, hashlib.sha256(request.encode()).hexdigest()]
    )
    key = f"AWS4{secret}".encode()
    for part in (day, region, "s3", "aws4_request"):
        key = hmac.new(key, part.encode(), hashlib.sha256).digest()
    signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    return {
        **{k: v for k, v in all_headers.items() if k != "host"},
        "Authorization": (
            f"AWS4-HMAC-SHA256 Credential={key_id}/{scope}, "
            f"SignedHeaders={';'.join(names)}, Signature={signature}"
        ),
    }
