from pydantic import BaseModel

from heydesk.runtime.schedule import WeeklyHours


class ReceptionistKnowledge(BaseModel):
    """What an office tells its receptionist. Its services are items (D17)."""

    address: str
    hours: WeeklyHours
    about: str = ""  # what the business does, who's on the team


def render(k: ReceptionistKnowledge, currency: str) -> str:
    """Knowledge as compact prompt text: short lines, not JSON."""
    lines = [f"Address: {k.address}", f"Office hours: {k.hours.render()}"]
    if k.about.strip():
        lines += ["", "About us:", k.about.strip()]
    return "\n".join(lines)
