"""Words and chunks: how text is cut up for search, and tidied to one line. Pure."""

import re
import unicodedata
from difflib import SequenceMatcher

LETTERS = {"L", "M", "N"}  # letters, combining marks (Bengali vowel signs), numbers


def one_line(text: str, limit: int | None = None) -> str:
    """`text` on one line with single spaces, cut to `limit` characters: what a visitor or the
    model wrote, ready to save in a column of that size."""
    return " ".join(text.split())[:limit]


def words(text: str) -> list[str]:
    """Lowercase words, in any script. A mark stays with its letter (Python's `\\w` would cut a
    Bengali word at each vowel sign), and a plural "s" is dropped so "biryanis" finds "biryani"."""
    cleaned = "".join(c if unicodedata.category(c)[0] in LETTERS else " " for c in text.lower())
    return [w.removesuffix("s") if len(w) > 3 else w for w in cleaned.split()]


SENTENCE_END = re.compile(r"(?<=[.!?।])\s+")
HEADING = re.compile(r"#{1,6}\s")  # a Markdown heading starts a section


def chunk_text(text: str, size: int = 1200, overlap: int = 150) -> list[str]:
    """Pieces of about `size` characters that break at paragraphs, then sentences, then words,
    each starting with the tail of the one before it so an answer on a boundary isn't lost.

    A heading line (`# …`) stays with the paragraph below it, and starts a new chunk, so a
    section is searched on its own instead of mixed with its neighbours.
    """
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text.replace("\r\n", "\n")) if p.strip()]
    units: list[str] = []
    for paragraph in paragraphs:
        if units and HEADING.match(units[-1].lstrip()) and "\n" not in units[-1]:
            units[-1] += "\n" + paragraph  # a heading joins what it heads
        else:
            units.append(paragraph)
    pieces: list[str] = []
    for unit in units:
        pieces += _split(unit, size)
    chunks: list[str] = []
    current = ""
    for piece in pieces:
        if current and HEADING.match(piece):
            chunks.append(current)  # a new section: no overlap, it isn't a continuation
            current = piece
        elif current and len(current) + len(piece) + 2 > size:
            chunks.append(current)
            tail = _tail(current, overlap)
            current = f"{tail}\n\n{piece}" if tail else piece
        else:
            current = f"{current}\n\n{piece}" if current else piece
    if current.strip():
        chunks.append(current)
    return chunks


def _split(unit: str, size: int) -> list[str]:
    """One paragraph, cut at sentences (then words) if it's longer than `size`."""
    if len(unit) <= size:
        return [unit]
    out: list[str] = []
    current = ""
    for sentence in SENTENCE_END.split(unit):
        for part in _by_words(sentence, size):
            if current and len(current) + len(part) + 1 > size:
                out.append(current)
                current = part
            else:
                current = f"{current} {part}" if current else part
    if current:
        out.append(current)
    return out


def _by_words(sentence: str, size: int) -> list[str]:
    if len(sentence) <= size:
        return [sentence]
    out: list[str] = []
    current = ""
    for word in sentence.split():
        if current and len(current) + len(word) + 1 > size:
            out.append(current)
            current = word
        else:
            current = f"{current} {word}" if current else word
    return [*out, current] if current else out


def _tail(text: str, overlap: int) -> str:
    """The last `overlap` characters of `text`, starting at a word."""
    if overlap <= 0 or len(text) <= overlap:
        return ""
    tail = text[-overlap:]
    return tail[tail.find(" ") + 1 :] if " " in tail else ""


MIN_QUESTION_MATCH = 0.75  # how close two questions' words are to count as one


def group_alike(questions: list[str]) -> list[list[int]]:
    """The questions in groups of ones asked alike, each a list of their indexes, the
    biggest group first. Each joins the first group whose first question is close enough.
    ponytail: word likeness, not meaning; an embedding would group "delivery charge?" with
    "how much to send?", if the owner needs it."""
    groups: list[tuple[str, list[int]]] = []
    for i, question in enumerate(questions):
        said = " ".join(words(question))
        for first, members in groups:
            if SequenceMatcher(None, first, said).ratio() >= MIN_QUESTION_MATCH:
                members.append(i)
                break
        else:
            groups.append((said, [i]))
    return sorted((members for _, members in groups), key=len, reverse=True)
