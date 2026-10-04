from pydantic import BaseModel

from heydesk.runtime.schedule import WeeklyHours


class ReceptionistKnowledge(BaseModel):
    """What an office tells its receptionist. Its services are items (D17)."""

    address: str = ""  # left empty at first: the setup list says what's missing
    hours: WeeklyHours = WeeklyHours()
    about: str = ""  # what the business does, who's on the team


def render(k: ReceptionistKnowledge, currency: str) -> str:
    """Knowledge as compact prompt text: short lines, not JSON."""
    lines = []
    if k.address.strip():
        lines.append(f"Address: {k.address.strip()}")
    if k.hours.is_set():
        lines.append(f"Office hours: {k.hours.render()}")
    if k.about.strip():
        lines += ["", "About us:", k.about.strip()]
    return "\n".join(lines)
