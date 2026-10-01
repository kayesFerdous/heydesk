from pydantic import BaseModel, Field

from heydesk.runtime.schedule import WeeklyHours
from heydesk.runtime.speech import money


class MenuItem(BaseModel):
    id: str
    name: str
    description: str = ""
    price: int = Field(ge=0)  # minor units (paisa / cents), so sums are exact
    category: str
    tags: list[str] = []  # "vegetarian", "spicy", "popular", "contains-nuts"
    available: bool = True


class RestaurantKnowledge(BaseModel):
    address: str
    hours: WeeklyHours
    menu: list[MenuItem] = Field(min_length=1)
    policies: str = ""  # allergens disclaimer, delivery area…


def render(k: RestaurantKnowledge, currency: str) -> str:
    """Knowledge as compact prompt text. Deterministic: same input, same bytes, so the cache holds.

    Short lines, not JSON: the menu is the biggest part of every request (design §6).
    """
    lines = [
        f"Address: {k.address}",
        f"Opening hours: {k.hours.render()}",
        "",
        "Menu. Each line: name [id] price (tags): description",
    ]
    categories = list(dict.fromkeys(item.category for item in k.menu))  # in the customer's order
    for category in categories:
        lines.append(f"{category}:")
        for item in (i for i in k.menu if i.category == category):
            line = f"- {item.name} [{item.id}] {money(item.price, currency)}"
            if item.tags:
                line += f" ({', '.join(item.tags)})"
            if item.description:
                line += f": {item.description}"
            if not item.available:
                line += " NOT AVAILABLE"
            lines.append(line)
    if k.policies.strip():
        lines += ["", "Policies:", k.policies.strip()]
    return "\n".join(lines)
