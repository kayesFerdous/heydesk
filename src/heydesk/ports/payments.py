"""The `PaymentGateway` port: taking a payment online (D47). SSLCommerz is the one we use.

The gateway's word is the only proof a payment happened: what a visitor's browser posts
back is checked with the gateway before anything changes.
"""

from dataclasses import dataclass
from typing import Protocol


class PaymentUnavailable(Exception):
    """The gateway said no or couldn't be reached."""


@dataclass(frozen=True)
class Checkout:
    """One payment to start: what it's for, and where the gateway sends the buyer back."""

    order_id: str  # comes back with the payment, so we know which order it was
    amount: int  # minor units
    currency: str
    what: str  # "Delivery charge for O-101"
    name: str
    phone: str
    address: str
    back_url: str  # the buyer's browser comes back here: paid, failed or cancelled
    notify_url: str  # the gateway tells the server here, even if the buyer closes the tab


@dataclass(frozen=True)
class Paid:
    """A payment the gateway says went through."""

    order_id: str
    amount: int  # minor units
    currency: str
    reference: str  # the gateway's id for it, for the owner


class PaymentGateway(Protocol):
    async def start(self, checkout: Checkout) -> str:
        """The gateway's payment page, to send the buyer to."""
        ...

    async def check(self, payment_id: str) -> Paid | None:
        """Asks the gateway about a payment it posted back. None unless it went through."""
        ...
