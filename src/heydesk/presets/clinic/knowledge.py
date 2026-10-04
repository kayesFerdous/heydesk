from pydantic import BaseModel

from heydesk.runtime.schedule import WeeklyHours


class ClinicKnowledge(BaseModel):
    """What a clinic tells its front desk. Its doctors are items (D17), with their
    speciality as the category and their days, languages and so on as attributes (D27)."""

    address: str = ""  # left empty at first: the setup list says what's missing
    hours: WeeklyHours = WeeklyHours()  # the clinic's front desk; doctors have their own days
    emergency_phone: str = ""  # where to send someone who needs help now
    policies: str = ""  # insurance, what to bring, how fees are paid


def render(k: ClinicKnowledge, currency: str) -> str:
    """Knowledge as compact prompt text: short lines, not JSON."""
    lines = []
    if k.address.strip():
        lines.append(f"Address: {k.address.strip()}")
    if k.hours.is_set():
        lines.append(f"Front desk hours: {k.hours.render()}")
    if k.emergency_phone.strip():
        lines.append(f"Emergency number: {k.emergency_phone.strip()}")
    if k.policies.strip():
        lines += ["", "Policies:", k.policies.strip()]
    return "\n".join(lines)
