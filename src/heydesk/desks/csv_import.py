"""Rows from a CSV file a business already has (a spreadsheet saved as CSV). Pure.

Headers are matched loosely ("Price (৳)" → `price`, "Speciality" → `speciality`). A desk turns
each row into its own shape; every row's problems come back at once, and nothing is saved
unless the whole file is clean.
"""

import csv
import io
import re
from collections.abc import Callable

from pydantic import ValidationError

from heydesk.errors import InvalidConfig

MAX_ROWS = 2000
YES = {"yes", "y", "true", "1", "available", "in stock", ""}


def key(header: str) -> str:
    """ "Price (৳)" → "price", "Start Time" → "start_time"."""
    words = re.sub(r"\(.*?\)", "", header).strip().lower()
    return re.sub(r"[^a-z0-9]+", "_", words).strip("_")


def money(text: str) -> int:
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


def parse[T](text: str, needs: set[str], row: Callable[[dict[str, str]], T]) -> list[T]:
    """Every row through `row`, which gets the cells by their `key()`. Raises InvalidConfig
    with one problem per bad row ("row 3": "…")."""
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    columns = {key(h) for h in reader.fieldnames or []}
    if not needs <= columns:
        raise InvalidConfig({"csv": f"needs columns: {', '.join(sorted(needs - columns))}"})
    found, problems = [], {}
    for n, cells in enumerate(reader, start=2):  # row 1 is the header
        if n > MAX_ROWS + 1:
            raise InvalidConfig({"csv": f"more than {MAX_ROWS} rows; split it into files"})
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
