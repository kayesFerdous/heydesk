"""Rows from a CSV file a business already has (a spreadsheet saved as CSV). Pure.

Their columns become our fields by a `mapping` the owner checked ({"price": "MRP"}).
`suggest` makes the first one from the column names alone, and without a mapping
columns match by name ("Price (৳)" → `price`). A desk turns each row into its own shape; every
row's problems come back at once, and nothing is saved unless the whole file is clean.
"""

import csv
import io
import re
from collections.abc import Callable
from difflib import SequenceMatcher

from pydantic import BaseModel, ValidationError

from heydesk.errors import InvalidConfig

MAX_ROWS = 2000
YES = {"yes", "y", "true", "1", "available", "in stock", ""}
SAMPLE_ROWS = 3  # what the owner's screen shows of a file
CLOSE = 0.8  # how alike a misspelt column name must be ("prise", "colors") to count


class Column(BaseModel):
    """One of a desk's fields, as the owner sees it when matching columns."""

    name: str  # "price"
    about: str  # what it holds, in plain words: "its price: 1850 or ৳1,850"
    required: bool = False
    aliases: list[str] = []  # other names a sheet gives it: "mrp", "dam", "দাম"


class Suggestion(BaseModel):
    """What the owner's screen for matching columns needs."""

    columns: list[Column]  # our fields
    headers: list[str]  # the file's columns, as they're spelled there
    samples: list[dict[str, str]]  # its first few rows
    mapping: dict[str, str]  # our field → their column; a field with no column is left out


def needs(columns: list[Column]) -> set[str]:
    return {c.name for c in columns if c.required}


def reader(text: str) -> csv.DictReader[str]:
    return csv.DictReader(io.StringIO(text.lstrip("\ufeff")))


def preview_csv(text: str) -> tuple[list[str], list[dict[str, str]]]:
    """The file's column names and its first few rows, as they are."""
    rows = reader(text)
    headers = [h for h in rows.fieldnames or [] if h and h.strip()]
    if not headers:
        raise InvalidConfig({"csv": "the file has no header row"})
    samples: list[dict[str, str]] = []
    for cells in rows:
        if len(samples) == SAMPLE_ROWS:
            break
        if any((cells.get(h) or "").strip() for h in headers):
            samples.append({h: (cells.get(h) or "").strip() for h in headers})
    return headers, samples


def suggest(text: str, columns: list[Column]) -> Suggestion:
    """The file's columns, a few rows, and which column is which of our fields, for the owner
    to check and change."""
    headers, samples = preview_csv(text)
    return Suggestion(
        columns=columns, headers=headers, samples=samples, mapping=match(headers, columns)
    )


def match(headers: list[str], columns: list[Column]) -> dict[str, str]:
    """Our field → the file's column, best matches first, each column used once. A field
    whose names fit no column is left out."""
    scored = []
    for header in headers:
        for column in columns:
            score = max(column_match_score(header, name) for name in [column.name, *column.aliases])
            if score:
                scored.append((score, column.name, header))
    mapping: dict[str, str] = {}
    for _, field, header in sorted(scored, key=lambda s: -s[0]):  # stable: our order on a tie
        if field not in mapping and header not in mapping.values():
            mapping[field] = header
    return mapping


def column_match_score(header: str, name: str) -> float:
    """How well a column's name fits one of a field's names: 1 the same ("Price (৳)" and
    "price"), 0.9 inside it ("Selling Price"), or how alike a misspelling is; 0 otherwise."""
    theirs, ours = split_words(header), split_words(name)
    if not theirs or not ours:
        return 0
    if theirs == ours:
        return 1
    n = len(ours)
    if any(theirs[i : i + n] == ours for i in range(len(theirs) - n + 1)):
        return 0.9
    alike = SequenceMatcher(None, " ".join(theirs), " ".join(ours)).ratio()
    return alike * 0.85 if alike >= CLOSE else 0  # below an exact or a whole-word match


def split_words(text: str) -> list[str]:
    """ "Product's Name (Bangla)" → ["product", "s", "name"]; Bangla letters are kept."""
    plain = re.sub(r"\(.*?\)", "", text).lower()
    return re.findall(r"[a-z0-9\u0980-\u09ff]+", plain)


def by_name(headers: list[str], columns: list[Column]) -> dict[str, str]:
    """Each of our fields whose name the file already uses ("Price (৳)" → price)."""
    ours = {c.name for c in columns}
    return {key(h): h for h in reversed(headers) if key(h) in ours}


def key(header: str) -> str:
    """ "Price (৳)" → "price", "Start Time" → "start_time"."""
    words = re.sub(r"\(.*?\)", "", header).strip().lower()
    return re.sub(r"[^a-z0-9]+", "_", words).strip("_")


def parse_money(text: str) -> int:
    """ "৳1,200.50" or "1200" → minor units (120050)."""
    cleaned = re.sub(r"[^\d.]", "", text)
    try:
        return round(float(cleaned) * 100)
    except ValueError:
        raise ValueError(f"{text!r} isn't an amount") from None


def items(text: str) -> list[str]:
    """ "sat; sun", "Sat, Sun", "english|bangla" → a list."""
    return [part.strip() for part in re.split(r"[;|,/]", text) if part.strip()]


def available(text: str) -> bool:
    return text.strip().lower() in YES


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:50] or "item"


def parse[T](
    text: str,
    needs: set[str],
    row: Callable[[dict[str, str]], T],
    mapping: dict[str, str] | None = None,
) -> list[T]:
    """Every row through `row`, which gets the cells by our field names: through `mapping`
    (our field → their column) when there is one, by `key()` otherwise. Raises InvalidConfig
    with one problem per bad row ("row 3": "…")."""
    rows = reader(text)
    headers = rows.fieldnames or []
    if mapping is not None:
        absent = [h for h in mapping.values() if h not in headers]
        if absent:
            raise InvalidConfig({"mapping": f"the file has no column {absent[0]!r}"})
        columns = set(mapping)
    else:
        columns = {key(h) for h in headers}
    if not needs <= columns:
        raise InvalidConfig({"csv": f"needs columns: {', '.join(sorted(needs - columns))}"})
    found, problems = [], {}
    for n, cells in enumerate(rows, start=2):  # row 1 is the header
        if n > MAX_ROWS + 1:
            raise InvalidConfig({"csv": f"more than {MAX_ROWS} rows; split it into files"})
        if mapping is not None:
            clean = {field: (cells.get(h) or "").strip() for field, h in mapping.items()}
        else:
            clean = {key(k): (v or "").strip() for k, v in cells.items() if k}
        if not any(clean.values()):
            continue  # a blank line
        try:
            found.append(row(clean))
        except ValidationError as error:
            problems[f"row {n}"] = "; ".join(
                f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in error.errors()
            )
        except ValueError as error:
            problems[f"row {n}"] = str(error)
    if problems:
        raise InvalidConfig(problems)
    return found
