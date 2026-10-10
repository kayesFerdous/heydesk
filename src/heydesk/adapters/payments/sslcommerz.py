"""SSLCommerz behind the `PaymentGateway` port (D47), over httpx.

Starting a payment makes a session and returns its page. When the buyer is done, SSLCommerz
posts a `val_id` to the back and notify URLs; `check` asks its validation API about it, so a
forged post changes nothing. Docs: developer.sslcommerz.com/doc/v4/
"""

from decimal import Decimal

import httpx

from heydesk.ports.payments import Checkout, Paid, PaymentUnavailable

SANDBOX = "https://sandbox.sslcommerz.com"
LIVE = "https://securepay.sslcommerz.com"
NO_EMAIL = "customer@example.com"  # SSLCommerz requires one; we don't ask buyers for theirs


class SslCommerz:
    def __init__(
        self, store_id: str, password: str, sandbox: bool = True, timeout: float = 15.0
    ) -> None:
        self.store_id = store_id
        self.password = password
        self.base = SANDBOX if sandbox else LIVE
        self.client = httpx.AsyncClient(timeout=timeout)

    async def start(self, checkout: Checkout) -> str:
        form = {
            "store_id": self.store_id,
            "store_passwd": self.password,
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
        body = await self._call("post", "/gwprocess/v4/api.php", data=form)
        if body.get("status") != "SUCCESS" or not body.get("GatewayPageURL"):
            raise PaymentUnavailable(f"SSLCommerz: {body.get('failedreason') or 'no session'}")
        return str(body["GatewayPageURL"])

    async def check(self, payment_id: str) -> Paid | None:
        params = {
            "val_id": payment_id,
            "store_id": self.store_id,
            "store_passwd": self.password,
            "format": "json",
        }
        body = await self._call("get", "/validator/api/validationserverAPI.php", params=params)
        if body.get("status") not in ("VALID", "VALIDATED"):  # VALIDATED: asked about before
            return None
        return Paid(
            order_id=str(body["tran_id"]),
            amount=int(Decimal(str(body["amount"])) * 100),
            currency=str(body["currency"]),
            reference=str(body.get("bank_tran_id") or payment_id),
        )

    async def _call(self, method: str, path: str, **kw: object) -> dict[str, object]:
        try:
            res = await self.client.request(method, self.base + path, **kw)  # type: ignore[arg-type]
            body = res.json()
        except (httpx.HTTPError, ValueError):
            raise PaymentUnavailable("SSLCommerz didn't answer") from None
        if not isinstance(body, dict):
            raise PaymentUnavailable("SSLCommerz answered something odd")
        return body
