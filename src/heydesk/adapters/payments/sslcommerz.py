"""SSLCommerz behind the `PaymentGateway` port (D47, D49), over httpx.

Each call uses the business's own store. Starting a payment makes a session and returns its
page. When the buyer is done, SSLCommerz posts a `val_id` to the back and notify URLs;
`check` asks its validation API about it, so a forged post changes nothing.
Docs: developer.sslcommerz.com/doc/v4/
"""

import secrets
from decimal import Decimal
from typing import Any

import httpx

from heydesk.ports.payments import Account, Checkout, Paid, PaymentUnavailable

SANDBOX = "https://sandbox.sslcommerz.com"
LIVE = "https://securepay.sslcommerz.com"
NO_EMAIL = "customer@example.com"  # SSLCommerz requires one; we don't ask buyers for theirs
NOWHERE = "https://example.com/"  # where a check's session would send someone: no one goes


class SslCommerz:
    def __init__(self, timeout: float = 15.0) -> None:
        self.client = httpx.AsyncClient(timeout=timeout)

    async def verify(self, account: Account) -> None:
        """SSLCommerz has no "are these right?" call, so this starts a ৳10 session no one will
        ever pay: it only opens for a store id and password that are right."""
        check = Checkout(
            order_id=f"check-{secrets.token_hex(8)}",
            amount=1000,
            currency="BDT",
            what="Connection check",
            name="HeyDesk",
            phone="01700000000",
            address="",
            back_url=NOWHERE,
            notify_url=NOWHERE,
        )
        await self.start(account, check)

    async def start(self, account: Account, checkout: Checkout) -> str:
        form = {
            "store_id": account.store_id,
            "store_passwd": account.password,
            "total_amount": f"{Decimal(checkout.amount) / 100:.2f}",
            "currency": checkout.currency,
            "tran_id": checkout.order_id,
            "success_url": checkout.back_url,
            "fail_url": checkout.back_url,
            "cancel_url": checkout.back_url,
            "ipn_url": checkout.notify_url,
            "product_name": checkout.what,
            "product_category": "General",
            "product_profile": "general",
            "shipping_method": "NO",
            "cus_name": checkout.name,
            "cus_email": NO_EMAIL,
            "cus_phone": checkout.phone,
            "cus_add1": checkout.address or "Bangladesh",  # required; a clinic has none
            "cus_city": "Bangladesh",
            "cus_country": "Bangladesh",
        }
        body = await self._call(account, "post", "/gwprocess/v4/api.php", data=form)
        if body.get("status") != "SUCCESS" or not body.get("GatewayPageURL"):
            raise PaymentUnavailable(str(body.get("failedreason") or "SSLCommerz said no"))
        return str(body["GatewayPageURL"])

    async def check(self, account: Account, payment_id: str) -> Paid | None:
        params = {
            "val_id": payment_id,
            "store_id": account.store_id,
            "store_passwd": account.password,
            "format": "json",
        }
        path = "/validator/api/validationserverAPI.php"
        body = await self._call(account, "get", path, params=params)
        if body.get("status") not in ("VALID", "VALIDATED"):  # VALIDATED: asked about before
            return None
        return Paid(
            order_id=str(body["tran_id"]),
            amount=int(Decimal(str(body["amount"])) * 100),
            currency=str(body["currency"]),
            reference=str(body.get("bank_tran_id") or payment_id),
        )

    async def _call(self, account: Account, method: str, path: str, **kw: Any) -> dict[str, Any]:
        base = SANDBOX if account.sandbox else LIVE
        try:
            res = await self.client.request(method, base + path, **kw)
            body = res.json()
        except (httpx.HTTPError, ValueError):
            raise PaymentUnavailable("SSLCommerz didn't answer") from None
        if not isinstance(body, dict):
            raise PaymentUnavailable("SSLCommerz answered something odd")
        return body
