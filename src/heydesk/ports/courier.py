"""A shop's courier (D112): the agent books each confirmed order's parcel and follows it.

Only a fake courier sits behind it for now; Pathao and Steadfast come with production, each
with the shop's own merchant login (their old adapters are in git, commit b277938)."""

from dataclasses import dataclass
from typing import Literal, Protocol

type Ending = Literal["delivered", "returned"]


@dataclass(frozen=True)
class Parcel:
    code: str  # "O-101": the courier's invoice, unique, so a retry can't book twice
    name: str
    phone: str  # 01XXXXXXXXX
    address: str  # the address and the area, one line: the courier finds the place
    cod: int  # whole taka to collect
    items: str  # what's in it, for the rider
    note: str = ""


@dataclass(frozen=True)
class Tracked:
    said: str  # the courier's own words: "Picked up", "In transit"
    ending: Ending | None  # the order's last status, when the courier says it's over


class CourierError(Exception):
    """The courier couldn't do it: not reached, or the parcel refused. The message is for
    the owner."""


class CourierApi(Protocol):
    async def book(self, parcel: Parcel) -> str:
        """Books the parcel; returns the courier's parcel id."""
        ...

    async def status(self, parcel_id: str) -> Tracked: ...
