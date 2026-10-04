"""Text out of an uploaded file (D34): `.txt` and `.md` as they are, `.pdf` by its text layer."""

import io
from pathlib import PurePath

from pypdf import PdfReader
from pypdf.errors import PyPdfError

from heydesk.errors import UnreadableDocument

MAX_FILE_BYTES = 10_000_000
KINDS = {".txt": "text", ".md": "markdown", ".markdown": "markdown", ".pdf": "pdf"}


def kind_of(filename: str) -> str:
    kind = KINDS.get(PurePath(filename).suffix.lower())
    if kind is None:
        raise UnreadableDocument("Add a .txt, .md or .pdf file, or paste the text instead.")
    return kind


def extract_text(filename: str, content: bytes) -> str:
    """The file's text. Blocking (a big PDF takes a moment): call it in a thread."""
    if len(content) > MAX_FILE_BYTES:
        raise UnreadableDocument(f"That file is over {MAX_FILE_BYTES // 1_000_000} MB.")
    if kind_of(filename) != "pdf":
        try:
            return content.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise UnreadableDocument("That text file isn't UTF-8. Save it as UTF-8.") from None
    try:
        reader = PdfReader(io.BytesIO(content))
        if reader.is_encrypted:
            raise UnreadableDocument("That PDF is password-protected.")
        text = "\n\n".join(page.extract_text() or "" for page in reader.pages)
    except PyPdfError:
        raise UnreadableDocument("That doesn't look like a readable PDF.") from None
    if not text.strip():
        raise UnreadableDocument(
            "That PDF has no text we can read. If it's a scan of a page, paste the text instead."
        )
    return text
