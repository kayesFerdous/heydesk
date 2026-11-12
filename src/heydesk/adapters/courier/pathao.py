"""Pathao Courier's merchant API behind the `CourierApi` port, over httpx.

A token comes from the client id and secret with the merchant's login, and is kept until it
expires. The city, zone and area are left out of a booking: Pathao finds them from the address
("auto address"). Its sandbox takes Pathao's published test login.
"""

import time
from typing import Any

import httpx

from heydesk.ports.courier import CourierAccount, CourierError, FinalStatus, Parcel, ParcelStatus

LIVE = "https://api-hermes.pathao.com"
SANDBOX = "https://courier-api-sandbox.pathao.com"
NORMAL_DELIVERY = 48  # delivery_type: normal, not on-demand
PARCEL_ITEM_TYPE = 2  # item_type: a parcel, not a document
PARCEL_WEIGHT_KG = (
    0.5  # kg. Limitation: one weight for every parcel; a product weight when shops need it
)
FINAL_STATUSES: dict[
    str, FinalStatus
] = {  # Pathao's final words, lower case → the order's last status
    "delivered": "delivered",
    "partial_delivery": "delivered",
    "return": "returned",
    "returned": "returned",
    "paid_return": "returned",
}


class Pathao:
    def __init__(self, timeout: float = 15.0) -> None:
        self.client = httpx.AsyncClient(timeout=timeout)
        self.tokens: dict[tuple[bool, str, str], tuple[str, float]] = {}  # → (token, expires)

    async def verify_account(self, account: CourierAccount) -> int | None:
        data = await self._call(account, "GET", "stores")
        stores = (data.get("data") or {}).get("data") or []
        ids = [s["store_id"] for s in stores if s.get("is_active", 1)]
        if account.store_id is not None:
            if account.store_id not in ids:
                raise CourierError(f"Pathao has no active store {account.store_id} on this login.")
            return account.store_id
        if not ids:
            raise CourierError("Add a pick-up store in Pathao's merchant panel first.")
        return int(ids[0])

    async def book(self, account: CourierAccount, parcel: Parcel) -> str:
        body = {
            "store_id": account.store_id,
            "merchant_order_id": parcel.code,
            "recipient_name": parcel.name,
            "recipient_phone": parcel.phone,
            "recipient_address": parcel.address[:220],
            "delivery_type": NORMAL_DELIVERY,
            "item_type": PARCEL_ITEM_TYPE,
            "special_instruction": parcel.note,
            "item_quantity": parcel.quantity,
            "item_weight": PARCEL_WEIGHT_KG,
            "item_description": parcel.items,
            "amount_to_collect": parcel.cash_on_delivery,
        }
        data = (await self._call(account, "POST", "orders", json=body)).get("data") or {}
        if not data.get("consignment_id"):
            raise CourierError("Pathao didn't book it.")
        return str(data["consignment_id"])

    async def status(self, account: CourierAccount, parcel_id: str) -> ParcelStatus:
        data = (await self._call(account, "GET", f"orders/{parcel_id}/info")).get("data") or {}
        said = str(data.get("order_status_slug") or data.get("order_status") or "")
        return ParcelStatus(said, FINAL_STATUSES.get(said.lower().replace(" ", "_")))

    async def _token(self, account: CourierAccount) -> str:
        key = (account.sandbox, account.key, account.username)
        kept = self.tokens.get(key)
        if kept and kept[1] > time.monotonic():
            return kept[0]
        body = {
            "client_id": account.key,
            "client_secret": account.secret,
            "grant_type": "password",
            "username": account.username,
            "password": account.password,
        }
        response = await self._send(account, "POST", "issue-token", json=body)
        if response.status_code in (400, 401, 422):
            raise CourierError("Pathao refused the login: check the client id, secret and login.")
        data = self._check(response)
        token = str(data.get("access_token", ""))
        if not token:
            raise CourierError("Pathao refused the login: check the client id, secret and login.")
        expires = time.monotonic() + int(data.get("expires_in", 3600)) - 60
        self.tokens[key] = (token, expires)
        return token

    async def _call(
        self, account: CourierAccount, method: str, path: str, **kwargs: Any
    ) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {await self._token(account)}"}
        response = await self._send(account, method, path, headers=headers, **kwargs)
        if response.status_code == 401:  # the token ended early: a new one, once
            self.tokens.pop((account.sandbox, account.key, account.username), None)
            headers = {"Authorization": f"Bearer {await self._token(account)}"}
            response = await self._send(account, method, path, headers=headers, **kwargs)
        return self._check(response)

    def _check(self, response: httpx.Response) -> dict[str, Any]:
        """The answer when it worked; otherwise `CourierError` with a line for the owner."""
        try:
            data: dict[str, Any] = response.json()
        except ValueError:
            data = {}
        if response.is_success:
            return data
        errors = data.get("errors") or {}
        said = "; ".join(
            " ".join(v) if isinstance(v, list) else str(v) for v in errors.values()
        ) or str(data.get("message", ""))
        raise CourierError(f"Pathao answered {response.status_code}. {said}".strip())

    async def _send(
        self, account: CourierAccount, method: str, path: str, **kwargs: Any
    ) -> httpx.Response:
        base = SANDBOX if account.sandbox else LIVE
        try:
            return await self.client.request(method, f"{base}/aladdin/api/v1/{path}", **kwargs)
        except httpx.HTTPError:
            raise CourierError("Couldn't reach Pathao. Try again in a minute.") from None
