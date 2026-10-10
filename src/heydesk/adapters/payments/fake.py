"""FakePayments: a gateway for tests. A test says which payments went through in `paid`."""

from heydesk.ports.payments import Checkout, Paid


class FakePayments:
    def __init__(self) -> None:
        self.started: list[Checkout] = []
        self.paid: dict[str, Paid] = {}  # payment id -> what the gateway would say

    async def start(self, checkout: Checkout) -> str:
        self.started.append(checkout)
        return f"https://pay.test/{checkout.order_id}"

    async def check(self, payment_id: str) -> Paid | None:
        return self.paid.get(payment_id)
