"""Where a shop delivers: its areas, each with a charge and its districts. Pure."""

from pydantic import BaseModel, Field, field_validator

from heydesk.runtime.speech import money
from heydesk.shop.districts import DISTRICTS, district


class Zone(BaseModel):
    """Where the shop delivers, and what that costs: "Inside Dhaka", ৳60, "1–2 days". It covers
    its `districts`; one with none covers everywhere else."""

    name: str = Field(min_length=1, max_length=100)
    charge: int = Field(ge=0)  # minor units
    days: str = Field(default="", max_length=50)  # how long it usually takes
    districts: list[str] = []  # of Bangladesh's 64, spelled as in `DISTRICTS`
    # The buyer pays this area's charge online first, and that confirms the order: BD
    # shops ask it outside Dhaka, where a refused parcel costs the most
    pay_first: bool = False

    @field_validator("districts")
    @classmethod
    def known_districts(cls, names: list[str]) -> list[str]:
        return [district(name) for name in names if name.strip()]

    @property
    def everywhere_else(self) -> bool:
        return not self.districts

    def prompt_line(self, currency: str) -> str:
        line = f"- {self.name}: {money(self.charge, currency)}"
        line += f", usually {self.days}" if self.days else ""
        return line + f" ({', '.join(self.districts) or 'everywhere else'})"


def match_zone(zones: list[Zone], said_district: str) -> Zone:
    """The delivery area for an address in `said_district`: the one with that district,
    else the one for everywhere else. Raises ValueError."""
    name = district(said_district)
    found = next((z for z in zones if name in z.districts), None)
    found = found or next((z for z in zones if z.everywhere_else), None)
    if found:
        return found
    raise ValueError(f"The shop doesn't deliver to {name}. Say so, kindly.")


ADDRESS_LIMIT = 270  # leaves room for `with_district` in the column's 300


def with_district(address: str, said_district: str) -> str:
    """The address as saved, with its district in it. The district picks the delivery area and
    isn't kept apart, and the order page asks for it in its own box, so without this the
    address the shop and the agent read back later has no district. Raises ValueError."""
    name = district(said_district)
    text = address.lower()
    if name.lower() in text or DISTRICTS[name] in address:
        return address
    return f"{address}, {name}"
