"""Documents through the API (D34): added as text or a file, listed, deleted, and private."""

import io

from fastapi import FastAPI
from httpx import AsyncClient
from pypdf import PdfWriter

from tests.conftest import new_business

POLICY = "# Refunds\n\nRefunds are given within seven days with the receipt.\n\nParking is free."


def pdf_with(text: str) -> bytes:
    """A one-page PDF whose text layer says `text` (built by hand: pypdf can't write text)."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
            b"/Resources << /Font << /F1 5 0 R >> >> >>"
        ),
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = b"%PDF-1.4\n", []
    for n, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % n + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF" % (len(objects) + 1, xref)
    return out


async def test_text_is_added_listed_read_and_deleted(client: AsyncClient) -> None:
    added = await client.post("/api/agent/documents", json={"title": "Policies", "text": POLICY})

    assert added.status_code == 201
    doc = added.json()
    assert (doc["title"], doc["kind"], doc["chunk_count"]) == ("Policies", "markdown", 1)
    listed = (await client.get("/api/agent/documents")).json()
    assert [d["id"] for d in listed] == [doc["id"]]
    assert (await client.get(f"/api/agent/documents/{doc['id']}")).json()["text"] == POLICY

    assert (await client.delete(f"/api/agent/documents/{doc['id']}")).status_code == 204
    assert (await client.get("/api/agent/documents")).json() == []
    assert (await client.delete(f"/api/agent/documents/{doc['id']}")).status_code == 404


async def test_a_markdown_file_and_a_pdf_are_read(client: AsyncClient) -> None:
    md = await client.post(
        "/api/agent/documents/file", files={"file": ("faq.md", POLICY.encode(), "text/markdown")}
    )
    pdf = await client.post(
        "/api/agent/documents/file",
        files={"file": ("fees.pdf", pdf_with("General visit costs 400 taka"), "application/pdf")},
        data={"title": "Fee list"},
    )

    assert (md.json()["title"], md.json()["kind"], md.json()["filename"]) == (
        "faq.md",
        "markdown",
        "faq.md",
    )
    assert (pdf.json()["title"], pdf.json()["kind"]) == ("Fee list", "pdf")
    text = (await client.get(f"/api/agent/documents/{pdf.json()['id']}")).json()["text"]
    assert "General visit costs 400 taka" in text


async def test_files_we_cant_read_are_refused_with_a_reason(client: AsyncClient) -> None:
    async def upload(name: str, data: bytes) -> dict[str, object]:
        r = await client.post("/api/agent/documents/file", files={"file": (name, data, "x/y")})
        assert r.status_code == 422
        return r.json()["error"]

    assert "Add a .txt, .md or .pdf" in (await upload("sheet.xlsx", b"x"))["message"]  # type: ignore[operator]
    assert "UTF-8" in (await upload("a.txt", b"\xff\xfe\x00bad"))["message"]  # type: ignore[operator]
    assert "readable PDF" in (await upload("a.pdf", b"not a pdf"))["message"]  # type: ignore[operator]
    blank = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(100, 100)
    writer.write(blank)
    assert "no text we can read" in (await upload("scan.pdf", blank.getvalue()))["message"]  # type: ignore[operator]
    empty = await client.post("/api/agent/documents", json={"title": "x", "text": "   "})
    assert empty.status_code == 422 and empty.json()["error"]["code"] == "unreadable_document"


async def test_a_business_sees_only_its_own_documents(app: FastAPI, client: AsyncClient) -> None:
    mine = (
        await client.post("/api/agent/documents", json={"title": "Mine", "text": POLICY})
    ).json()
    other = await new_business(app)
    try:
        await other.post("/api/agent", json={"preset": "restaurant"})

        assert (await other.get("/api/agent/documents")).json() == []
        assert (await other.get(f"/api/agent/documents/{mine['id']}")).status_code == 404
        assert (await other.delete(f"/api/agent/documents/{mine['id']}")).status_code == 404
        assert len((await client.get("/api/agent/documents")).json()) == 1  # still there
    finally:
        await other.aclose()


async def test_a_business_with_no_agent_yet_cant_add_documents(app: FastAPI) -> None:
    fresh = await new_business(app, "fresh@example.com")
    try:
        r = await fresh.post("/api/agent/documents", json={"title": "x", "text": "y"})
        assert r.status_code == 404
    finally:
        await fresh.aclose()
