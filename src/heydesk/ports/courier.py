"""A shop's courier (D90, D112): the agent books each confirmed order's parcel and follows it.

Steadfast and Pathao, with the shop's own merchant login (`courier_links`), and a fake courier
for a shop with none, outside production."""

from dataclasses import dataclass
from typing import Literal, Protocol

type Ending = Literal["delivered", "returned"]


@dataclass(frozen=True)
class CourierAccount:
    kind: str  # "steadfast", "pathao" or "fake"
    key: str = ""  # Steadfast's API key; Pathao's client id
    secret: str = ""  # Steadfast's secret key; Pathao's client secret
    username: str = ""  # Pathao: the merchant login
    password: str = ""
    store_id: int | None = None  # Pathao: the pick-up store
    sandbox: bool = False  # Pathao's sandbox


@dataclass(frozen=True)
class Parcel:
    code: str  # "O-101": the courier's invoice, unique, so a retry can't book twice
    name: str
    phone: str  # 01XXXXXXXXX
    address: str  # the address and the area, one line: the courier finds the place
    cod: int  # whole taka to collect
    items: str  # what's in it, for the rider
    quantity: int
    note: str = ""


@dataclass(frozen=True)
class Tracked:
    said: str  # the courier's own words: "in_review", "In Transit"
    ending: Ending | None  # the order's last status, when the courier says it's over


class CourierError(Exception):
    """The courier couldn't do it: not reached, the keys refused, or the parcel refused. The
    message is for the owner."""


class CourierApi(Protocol):
    async def check(self, account: CourierAccount) -> int | None:
        """The keys work. Pathao: the store to send from (the account's, or the first active
        one); the others: None."""
        ...

    async def book(self, account: CourierAccount, parcel: Parcel) -> str:
        """Books the parcel; returns the courier's parcel id."""
        ...

    async def status(self, account: CourierAccount, parcel_id: str) -> Tracked: ...
