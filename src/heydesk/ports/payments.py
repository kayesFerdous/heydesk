"""The `PaymentGateway` port: taking a payment online (D47). Two gateways sit behind it:
SSLCommerz (cards, mobile wallets, banks) and bKash on its own (D109).

Every call takes the business's own `Account` (D49): each business is paid into its own
store, so there's no one account for the platform. The gateway's word is the only proof a
payment happened: what a visitor's browser posts back is checked with it first.
"""

from dataclasses import dataclass
from typing import Literal, Protocol

type Provider = Literal["sslcommerz", "bkash"]
PROVIDERS: tuple[Provider, ...] = ("sslcommerz", "bkash")  # the order the pay page shows them


class PaymentUnavailable(Exception):
    """The gateway said no or couldn't be reached. Never carries the password."""


@dataclass(frozen=True)
class Account:
    """A business's account with one gateway. SSLCommerz needs a store id and its password;
    bKash an app key and secret, a username and a password (D109)."""

    store_id: str  # SSLCommerz's; empty for bKash
    password: str  # SSLCommerz's store password, or bKash's password
    sandbox: bool  # the gateway's test mode: test cards or wallets, no real money
    app_key: str = ""  # bKash's
    app_secret: str = ""  # bKash's
    username: str = ""  # bKash's


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
    async def verify(self, account: Account) -> None:
        """Raises PaymentUnavailable, with the gateway's reason, unless it accepts the account."""
        ...

    async def start(self, account: Account, checkout: Checkout) -> str:
        """The gateway's payment page, to send the buyer to."""
        ...

    async def check(self, account: Account, payment_id: str) -> Paid | None:
        """Asks the gateway about a payment it posted back. None unless it went through."""
        ...


type Gateways = dict[Provider, PaymentGateway]  # one of each, picked by the account's provider
