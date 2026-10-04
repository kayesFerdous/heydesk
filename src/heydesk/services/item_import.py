"""Items from a CSV (D29): a business's own spreadsheet, whatever its columns are called.

Columns we know become the item's fields; every other column becomes an attribute. A
`mapping` renames a business's headers ("Product Title" → "name"). Pure: no database here.
"""

import csv
import io
import re
from decimal import Decimal, InvalidOperation

from pydantic import ValidationError

from heydesk.errors import InvalidConfig
from heydesk.runtime.items import AttrValue, Item

MAX_ROWS = 5000
FIELDS = {"code", "name", "description", "price", "category", "tags", "available"}
YES = {"yes", "y", "true", "1", "available", "in stock"}
NO = {"no", "n", "false", "0", "unavailable", "out of stock"}


def _key(header: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", header.strip().lower()).strip("_")


def _price(text: str) -> int:
    """ "৳1,200.50", "1200" → minor units. Raises ValueError."""
    cleaned = re.sub(r"[^\d.]", "", text.replace(",", ""))
    try:
        return int((Decimal(cleaned) * 100).to_integral_value())
    except InvalidOperation:
        raise ValueError(f"{text!r} isn't a price") from None


def _value(text: str) -> AttrValue:
    """A cell as a fact: a number, yes/no, a list (separated by ; or |), or text."""
    text = text.strip()
    if ";" in text or "|" in text:
        return [part.strip() for part in re.split(r"[;|]", text) if part.strip()]
    if text.lower() in ("true", "false"):
        return text.lower() == "true"
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return text


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "item"


def parse_items(text: str, mapping: dict[str, str] | None = None) -> tuple[list[Item], list[str]]:
    """The items in the CSV, and the names of the columns that became attributes.

    Every problem comes back at once as `InvalidConfig` with a message per row, so the
    business can fix its sheet in one go; nothing is half-read.
    """
    renames = {_key(header): _key(field) for header, field in (mapping or {}).items()}
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    columns = {raw: renames.get(_key(raw), _key(raw)) for raw in reader.fieldnames or [] if raw}
    if not {"name", "price"} <= set(columns.values()):
        raise InvalidConfig(
            {"csv": "needs a column for the name and one for the price; use `mapping` to say which"}
        )
    attributes = [c for c in dict.fromkeys(columns.values()) if c not in FIELDS]
    items: list[Item] = []
    problems: dict[str, str] = {}
    codes: set[str] = set()
    for number, row in enumerate(reader, start=2):  # row 1 is the header
        if number - 1 > MAX_ROWS:
            raise InvalidConfig({"csv": f"more than {MAX_ROWS} rows; split it into files"})
        cells = {columns[raw]: (cell or "").strip() for raw, cell in row.items() if raw in columns}
        if not any(cells.values()):
            continue  # a blank line
        try:
            item = _item(cells, attributes, codes)
        except (ValueError, ValidationError) as error:
            problems[f"row {number}"] = _say(error)
            continue
        codes.add(item.code)
        items.append(item)
    if problems:
        raise InvalidConfig(problems)
    return items, attributes


def _say(error: ValueError | ValidationError) -> str:
    if isinstance(error, ValidationError):
        return "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in error.errors())
    return str(error)


def _item(cells: dict[str, str], attributes: list[str], taken: set[str]) -> Item:
    code = cells.get("code") or _slug(cells.get("name", ""))
    if cells.get("code") and code in taken:
        raise ValueError(f"the code {code!r} is used twice")
    base, n = code, 2
    while code in taken:  # a made-up code that clashes: "tea", "tea-2"
        code, n = f"{base}-{n}", n + 1
    for needed in ("name", "price"):
        if not cells.get(needed):
            raise ValueError(f"{needed} is empty")
    available = cells.get("available", "").lower()
    if available and available not in YES | NO:
        raise ValueError(f"available should be yes or no, not {available!r}")
    return Item(
        code=code,
        name=cells.get("name", ""),
        description=cells.get("description", ""),
        price=_price(cells["price"]),
        category=cells.get("category") or "General",
        tags=[t.strip().lower() for t in re.split(r"[;,|]", cells.get("tags", "")) if t.strip()],
        available=available not in NO,
        attributes={a: _value(cells[a]) for a in attributes if cells.get(a)},
    )
