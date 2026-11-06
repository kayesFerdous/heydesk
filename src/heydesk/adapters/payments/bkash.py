"""bKash behind the `PaymentGateway` port: its Tokenized Checkout, over httpx.

Each call grants a fresh token with the account's four keys, then makes its one call. bKash's
token lasts an hour and could be kept, but a payment is a few calls, so this keeps it simple.

Starting a payment creates it and returns bKash's page. When the buyer is done, bKash sends
their browser to the back URL with `paymentID` and `status`; only "success" is worth asking
about. `check` executes the payment (that's when the money moves) and reads what bKash says.
If execute fails (it was executed already, or timed out), it asks for the payment's status.
bKash has no server-to-server notice in this flow, so `notify_url` isn't used: the money moves
only when we execute, so a buyer who closes the tab first isn't charged.
Docs: developer.bka.sh (Tokenized Checkout, URL based, v1.2.0-beta)
"""

from decimal import Decimal
from typing import Any

import httpx

from heydesk.ports.payments import Account, Checkout, Paid, PaymentUnavailable

SANDBOX = "https://tokenized.sandbox.bka.sh/v1.2.0-beta/tokenized"
LIVE = "https://tokenized.pay.bka.sh/v1.2.0-beta/tokenized"
BKASH_SUCCESS = "0000"  # bKash's statusCode for "done"


class BkashCheckout:
    def __init__(self, timeout: float = 30.0) -> None:
        self.client = httpx.AsyncClient(timeout=timeout)

    async def verify(self, account: Account) -> None:
        """Keys that grant a token are keys bKash accepts."""
        await self._token(account)

    async def start(self, account: Account, checkout: Checkout) -> str:
        body = await self._call(
            account,
            "/checkout/create",
            {
                "mode": "0011",  # the buyer pays on bKash's page, nothing saved
                "payerReference": checkout.phone or " ",
                "callbackURL": checkout.back_url,
                "amount": f"{Decimal(checkout.amount) / 100:.2f}",
                "currency": checkout.currency,
                "intent": "sale",
                "merchantInvoiceNumber": checkout.order_id,
            },
        )
        if body.get("statusCode") != BKASH_SUCCESS or not body.get("bkashURL"):
            raise PaymentUnavailable(str(body.get("statusMessage") or "bKash said no"))
        return str(body["bkashURL"])

    async def check(self, account: Account, payment_id: str) -> Paid | None:
        try:
            body = await self._call(account, "/checkout/execute", {"paymentID": payment_id})
        except PaymentUnavailable:
            body = {}
        if body.get("transactionStatus") != "Completed":  # executed before, or it timed out
            path = "/checkout/payment/status"
            body = await self._call(account, path, {"paymentID": payment_id})
        if body.get("transactionStatus") != "Completed":
            return None
        return Paid(
            order_id=str(body["merchantInvoiceNumber"]),
            amount=int(Decimal(str(body["amount"])) * 100),
            currency=str(body["currency"]),
            reference=str(body.get("trxID") or payment_id),
        )

    async def _token(self, account: Account) -> str:
        headers = {"username": account.username, "password": account.password}
        keys = {"app_key": account.app_key, "app_secret": account.app_secret}
        body = await self._post(account, "/checkout/token/grant", keys, headers)
        if not body.get("id_token"):
            raise PaymentUnavailable(str(body.get("statusMessage") or "bKash refused the keys"))
        return str(body["id_token"])

    async def _call(self, account: Account, path: str, data: dict[str, str]) -> dict[str, Any]:
        """One call with a fresh token."""
        headers = {"Authorization": await self._token(account), "X-App-Key": account.app_key}
        return await self._post(account, path, data, headers)

    async def _post(
        self, account: Account, path: str, data: dict[str, str], headers: dict[str, str]
    ) -> dict[str, Any]:
        base = SANDBOX if account.sandbox else LIVE
        headers = {**headers, "Accept": "application/json"}  # httpx adds the Content-Type
        try:
            res = await self.client.post(base + path, json=data, headers=headers)
            body = res.json()
        except (httpx.HTTPError, ValueError):
            raise PaymentUnavailable("bKash didn't answer") from None
        if not isinstance(body, dict):
            raise PaymentUnavailable("bKash answered something odd")
        return body
