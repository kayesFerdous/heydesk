"""A business's items: dishes, services, products (design §5). Every preset uses this one shape.

They live in the `items` table, not the agent's config. A small list goes into
the prompt whole; a big one is found with the `search_items` tool (D17).
"""

from typing import Literal

from pydantic import BaseModel, Field

from heydesk.runtime.speech import money

# A business's own facts about an item: a doctor's speciality and days, a snack's calories.
# Plain values only, so they can be shown to the model and filtered on (D27).
type AttrValue = str | int | float | bool | list[str]


class Item(BaseModel):
    code: str = Field(min_length=1, max_length=50)  # the short id the model uses: "chk-biryani"
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    price: int = Field(ge=0)  # minor units (paisa / cents), so sums are exact
    category: str
    tags: list[str] = []  # "vegetarian", "spicy", "popular", "contains-nuts"
    available: bool = True
    attributes: dict[str, AttrValue] = {}  # "days": ["sat", "sun"], "calories": 220


def attribute_text(value: AttrValue) -> str:
    return ", ".join(value) if isinstance(value, list) else str(value).lower()


def item_line(item: Item, currency: str) -> str:
    """One item as a prompt line: `- Chicken Biryani [chk-biryani] ৳450 (spicy): Basmati…`
    With attributes: `… {calories: 220, days: sat, sun}`."""
    line = f"- {item.name} [{item.code}] {money(item.price, currency)}"
    if item.tags:
        line += f" ({', '.join(item.tags)})"
    if item.description:
        line += f": {item.description}"
    if item.attributes:
        facts = ", ".join(f"{k}: {attribute_text(v)}" for k, v in item.attributes.items())
        line += f" {{{facts}}}"
    if not item.available:
        line += " NOT AVAILABLE"
    return line


def render_items(items: list[Item], label: str, currency: str, max_in_prompt: int) -> str:
    """The whole list when it's short; otherwise the categories and a pointer to `search_items`."""
    categories = list(dict.fromkeys(item.category for item in items))  # in the business's order
    if len(items) > max_in_prompt:
        return (
            f"{label}: {len(items)} items, too many to list here. Use search_items to find them.\n"
            f"Categories: {', '.join(categories)}"
        )
    header = f"{label}. Each line: name [code] price (tags): description"
    if any(item.attributes for item in items):
        header += " {extra facts}"
    lines = [header]
    for category in categories:
        lines.append(f"{category}:")
        lines += [item_line(i, currency) for i in items if i.category == category]
    return "\n".join(lines)


# Finding items (D28): the model says what the visitor means as a few fields, and this
# code, not the model, decides which items match. Done in Python over the agent's items.

FIND_LIMIT = 10


class Filter(BaseModel):
    field: str = Field(description="An attribute name, such as calories or speciality")
    op: Literal["equals", "at_most", "at_least", "includes"] = Field(
        description="equals: the same value. at_most / at_least: a number limit. "
        "includes: a list attribute contains the value (days includes sat)"
    )
    value: str | float = Field(description="What to compare with")


class ItemQuery(BaseModel):
    query: str = Field(
        default="", max_length=100, description="A few words: a name, an ingredient, a topic"
    )
    category: str = Field(default="", description="Only this category, such as Cardiology")
    tags: list[str] = Field(default=[], description="Only items with all of these tags")
    min_price: float | None = Field(
        default=None, description="Lowest price, in whole currency units"
    )
    max_price: float | None = Field(
        default=None, description="Highest price, in whole currency units"
    )
    filters: list[Filter] = Field(default=[], description="Conditions on an item's extra facts")


def _words(text: str) -> list[str]:
    return [w.removesuffix("s") if len(w) > 3 else w for w in text.lower().split()][:5]


def _searchable(item: Item) -> str:
    facts = " ".join(attribute_text(v) for v in item.attributes.values())
    return " ".join([item.name, item.category, item.description, *item.tags, facts]).lower()


def _passes(item: Item, f: Filter) -> bool:
    have = item.attributes.get(f.field)
    if have is None:
        return False
    if f.op == "includes":
        if isinstance(have, list):
            return str(f.value).lower() in (h.lower() for h in have)
        return str(f.value).lower() in str(have).lower()
    if f.op == "equals":
        if isinstance(have, list):
            return str(f.value).lower() in (h.lower() for h in have)
        return str(have).lower() == str(f.value).lower()
    if isinstance(have, bool) or not isinstance(have, int | float):
        return False
    try:
        limit = float(f.value)
    except ValueError:
        return False
    return have <= limit if f.op == "at_most" else have >= limit


def find_items(items: list[Item], q: ItemQuery) -> list[Item]:
    """The available items that fit every condition, best match first. `query` words
    match name, category, description, tags and facts; more words matched ranks higher."""
    words = _words(q.query)
    ranked: list[tuple[int, int, Item]] = []
    for position, item in enumerate(items):
        if not item.available:
            continue
        if q.category and q.category.lower() not in item.category.lower():
            continue
        if not {t.lower() for t in q.tags} <= {t.lower() for t in item.tags}:
            continue
        if q.min_price is not None and item.price < round(q.min_price * 100):
            continue
        if q.max_price is not None and item.price > round(q.max_price * 100):
            continue
        if not all(_passes(item, f) for f in q.filters):
            continue
        text = _searchable(item)
        hits = sum(w in text for w in words)
        if words and not hits:
            continue
        ranked.append((-hits, position, item))
    return [item for _, _, item in sorted(ranked, key=lambda r: r[:2])]
