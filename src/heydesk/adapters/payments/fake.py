"""FakePayments: a gateway for tests. A test says which payments went through in `paid`; the
store id "bad" is refused."""

from heydesk.ports.payments import Account, Checkout, Paid, PaymentUnavailable


class FakePayments:
    def __init__(self) -> None:
        self.started: list[tuple[Account, Checkout]] = []
        self.paid: dict[str, Paid] = {}  # payment id -> what the gateway would say

    async def verify(self, account: Account) -> None:
        if account.store_id == "bad":
            raise PaymentUnavailable("Store Credential Error Or Store is De-active")

    async def start(self, account: Account, checkout: Checkout) -> str:
        self.started.append((account, checkout))
        return f"https://pay.test/{checkout.order_id}"

    async def check(self, account: Account, payment_id: str) -> Paid | None:
        return self.paid.get(payment_id)
