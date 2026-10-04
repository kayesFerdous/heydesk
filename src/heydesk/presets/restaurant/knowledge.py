from pydantic import BaseModel

from heydesk.runtime.schedule import WeeklyHours


class RestaurantKnowledge(BaseModel):
    """What a restaurant tells its agent. The menu is its items, in the `items` table (D17)."""

    address: str = ""  # left empty at first: the setup list says what's missing
    hours: WeeklyHours = WeeklyHours()
    policies: str = ""  # allergens disclaimer, delivery area…


def render(k: RestaurantKnowledge, currency: str) -> str:
    """Knowledge as compact prompt text: short lines, not JSON."""
    lines = []
    if k.address.strip():
        lines.append(f"Address: {k.address.strip()}")
    if k.hours.is_set():
        lines.append(f"Opening hours: {k.hours.render()}")
    if k.policies.strip():
        lines += ["", "Policies:", k.policies.strip()]
    return "\n".join(lines)
