"""Rows from a CSV file a business already has (a spreadsheet saved as CSV). Pure.

Their columns become our fields by a `mapping` the owner checked ({"price": "MRP"}).
`suggest` makes the first one from the column names alone, and without a mapping
columns match by name ("Price (৳)" → `price`). The shop turns each row into a product; every
row's problems come back at once, and nothing is saved unless the whole file is clean.
`products_from_csv` and `PRODUCT_COLUMNS`, at the end, are the shop's use of it.
"""

import csv
import io
import re
from collections.abc import Callable
from difflib import SequenceMatcher

from pydantic import BaseModel, ValidationError

from heydesk.errors import InvalidConfig
from heydesk.shop.products import Product, Variant, grouped

MAX_ROWS = 2000
YES_WORDS = {"yes", "y", "true", "1", "available", "in stock", ""}
SAMPLE_ROWS = 3  # what the owner's screen shows of a file
MIN_COLUMN_MATCH = 0.8  # how alike a misspelt column name must be ("prise", "colors") to count


class Column(BaseModel):
    """One of a product's fields, as the owner sees it when matching columns."""

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
    return alike * 0.85 if alike >= MIN_COLUMN_MATCH else 0  # below an exact or a whole-word match


def split_words(text: str) -> list[str]:
    """ "Product's Name (Bangla)" → ["product", "s", "name"]; Bangla letters are kept."""
    plain = re.sub(r"\(.*?\)", "", text).lower()
    return re.findall(r"[a-z0-9\u0980-\u09ff]+", plain)


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
    return text.strip().lower() in YES_WORDS


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


# A shop's products from a CSV file

PRODUCT_COLUMNS = [
    Column(
        name="name",
        about="the product's name",
        required=True,
        aliases=[
            "product",
            "product name",
            "item",
            "item name",
            "title",
            "item title",
            "naam",
            "পণ্য",
            "পণ্যের নাম",
            "নাম",
        ],
    ),
    Column(
        name="price",
        about="its price: 1850 or ৳1,850",
        required=True,
        aliases=["mrp", "rate", "selling price", "sale price", "dam", "daam", "taka", "দাম", "মূল্য"],
    ),
    Column(
        name="was_price",
        about="its price before a sale; empty when not on sale",
        aliases=[
            "regular price",
            "old price",
            "original price",
            "compare at price",
            "ager dam",
            "আগের দাম",
        ],
    ),
    Column(
        name="category",
        about="a group it's listed under: Panjabi, Saree",
        aliases=["group", "type", "collection", "ক্যাটাগরি", "ধরন"],
    ),
    Column(
        name="description",
        about="a line or two about it",
        aliases=["details", "about", "desc", "বিবরণ"],
    ),
    Column(name="sizes", about='the sizes it comes in: "M; L; XL"', aliases=["size", "সাইজ"]),
    Column(
        name="colours",
        about='the colours it comes in: "white; black"',
        aliases=["colour", "color", "colors", "rong", "rang", "রং", "রঙ"],
    ),
    Column(
        name="stock",
        about='how many are left, or per size and colour: "M: 5; L: 0"; empty when not counted',
        aliases=["qty", "quantity", "stock qty", "inventory", "স্টক", "পরিমাণ"],
    ),
    Column(
        name="code",
        about="the shop's own short id or SKU for it",
        aliases=["sku", "id", "product id", "product code", "item code", "কোড"],
    ),
    Column(
        name="photos",
        about="links to its photos, up to 3: https://…jpg",
        aliases=["photo", "image", "images", "picture", "pic", "image url", "ছবি"],
    ),
    Column(
        name="videos",
        about="links to its videos, up to 3: https://youtu.be/…",
        aliases=["video", "youtube", "video link", "ভিডিও"],
    ),
    Column(
        name="goes_with",
        about='codes of up to 3 products that go with it: "pajama-white; tupi"',
        aliases=["goes with", "pair with", "pairs with", "related", "upsell", "add on"],
    ),
]


def products_from_csv(text: str, mapping: dict[str, str] | None = None) -> list[Product]:
    """Columns: name, price, and optionally was_price (before a sale), category, description,
    sizes ("M; L; XL"), colours, stock (a number, "M: 5; L White: 0" per size and colour, or
    empty: not counted), code, photos (links), videos (links) and goes_with (codes).
    `mapping` names them in the file."""

    def row(cells: dict[str, str]) -> Product:
        sizes = items(cells.get("sizes", ""))
        colours = items(cells.get("colours") or cells.get("colors", ""))
        stock, variants = stock_cell(cells.get("stock", ""), sizes, colours)
        was = cells.get("was_price", "")
        return Product.model_validate(
            {
                "code": cells.get("code") or slug(cells["name"]),
                "name": cells["name"],
                "description": cells.get("description", ""),
                "price": parse_money(cells["price"]),
                "was_price": parse_money(was) if was else None,
                "category": cells.get("category") or "Products",
                "sizes": sizes,
                "colours": colours,
                "stock": stock,
                "variants": variants,
                "photos": links(cells.get("photos", "")),
                "videos": links(cells.get("videos", "")),
                "goes_with": items(cells.get("goes_with", ""))[:3],
            }
        )

    return grouped(parse(text, needs(PRODUCT_COLUMNS), row, mapping))


def links(text: str) -> list[str]:
    """A photos (or videos) cell: up to 3 http(s) links, apart by spaces, commas, ";" or "|"."""
    found = [
        part for part in re.split(r"[\s,;|]+", text) if part.startswith(("http://", "https://"))
    ]
    return list(dict.fromkeys(found))[:3]


def stock_cell(text: str, sizes: list[str], colours: list[str]) -> tuple[int | None, list[Variant]]:
    """A CSV stock cell: "" (not counted), "12" (the product's own stock), or a count per
    size and colour, "M: 5; L: 0" or "L White: 2; L/Navy: 0". Raises ValueError."""
    text = text.strip()
    if not text:
        return None, []
    if text.isdigit():
        return int(text), []
    names = {  # "l white" → ("L", "White"); "l" → ("L", ""); "white" → ("", "White")
        " ".join(part for part in (size, colour) if part).lower(): (size, colour)
        for size in [*sizes, ""]
        for colour in [*colours, ""]
        if size or colour
    }
    variants = []
    for part in re.split(r"[;\n]", text):
        if not part.strip():
            continue
        said, _, count = part.rpartition(":")
        key = " ".join(said.replace("/", " ").lower().split())
        if key not in names or not count.strip().isdigit():
            raise ValueError(
                f"stock {part.strip()!r}: write a size or colour and a number, like M: 5"
            )
        size, colour = names[key]
        variants.append(Variant(size=size, colour=colour, stock=int(count)))
    return None, variants
