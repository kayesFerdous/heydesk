from pydantic import BaseModel

from heydesk.runtime.schedule import WeeklyHours


class RestaurantKnowledge(BaseModel):
    """What a restaurant tells its agent. The menu is its items, in the `items` table (D17)."""

    address: str
    hours: WeeklyHours
    policies: str = ""  # allergens disclaimer, delivery area…


def render(k: RestaurantKnowledge, currency: str) -> str:
    """Knowledge as compact prompt text: short lines, not JSON."""
    lines = [f"Address: {k.address}", f"Opening hours: {k.hours.render()}"]
    if k.policies.strip():
        lines += ["", "Policies:", k.policies.strip()]
    return "\n".join(lines)
