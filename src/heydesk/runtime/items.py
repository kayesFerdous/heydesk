"""A business's items: dishes, services, products (design §5). Every preset uses this one shape.

They live in the `items` table, not the agent's config. A small list goes into
the prompt whole; a big one is found with the `search_items` tool (D17).
"""

from pydantic import BaseModel, Field

from heydesk.runtime.speech import money


class Item(BaseModel):
    code: str = Field(min_length=1, max_length=50)  # the short id the model uses: "chk-biryani"
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    price: int = Field(ge=0)  # minor units (paisa / cents), so sums are exact
    category: str
    tags: list[str] = []  # "vegetarian", "spicy", "popular", "contains-nuts"
    available: bool = True


def item_line(item: Item, currency: str) -> str:
    """One item as a prompt line: `- Chicken Biryani [chk-biryani] ৳450 (spicy): Basmati…`"""
    line = f"- {item.name} [{item.code}] {money(item.price, currency)}"
    if item.tags:
        line += f" ({', '.join(item.tags)})"
    if item.description:
        line += f": {item.description}"
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
    lines = [f"{label}. Each line: name [code] price (tags): description"]
    for category in categories:
        lines.append(f"{category}:")
        lines += [item_line(i, currency) for i in items if i.category == category]
    return "\n".join(lines)
