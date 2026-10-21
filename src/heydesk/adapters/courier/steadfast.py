"""Steadfast Courier behind the `CourierApi` port (D90), over httpx.

The API key and secret go as headers on every call. Steadfast has no sandbox: a booking is a
real parcel. Its docs are the API guide in the merchant panel (steadfast.com.bd/user/api/guide).
"""

from typing import Any

import httpx

from heydesk.ports.courier import Booked, CourierAccount, CourierError, Ending, Parcel, Tracked

BASE = "https://portal.packzy.com/api/v1"
ENDINGS: dict[str, Ending] = {  # Steadfast's final words → the order's last status
    "delivered": "delivered",
    "partial_delivered": "delivered",
    "cancelled": "returned",  # not delivered: the parcel is coming back to the shop
}


class Steadfast:
    def __init__(self, timeout: float = 15.0) -> None:
        self.client = httpx.AsyncClient(timeout=timeout)

    async def check(self, account: CourierAccount) -> int | None:
        await self._call(account, "GET", "get_balance")
        return None

    async def book(self, account: CourierAccount, parcel: Parcel) -> Booked:
        body = {
            "invoice": parcel.code,
            "recipient_name": parcel.name,
            "recipient_phone": parcel.phone,
            "recipient_address": parcel.address[:490],
            "cod_amount": parcel.cod,
            "note": parcel.note[:480],
            "item_description": parcel.items[:255],
            "total_lot": parcel.quantity,
        }
        data = await self._call(account, "POST", "create_order", json=body)
        consignment = data.get("consignment") or {}
        if not consignment.get("consignment_id"):
            raise CourierError(f"Steadfast didn't book it. {data.get('message', '')}".strip())
        return Booked(str(consignment["consignment_id"]))

    async def status(self, account: CourierAccount, parcel_id: str) -> Tracked:
        data = await self._call(account, "GET", f"status_by_cid/{parcel_id}")
        said = str(data.get("delivery_status", ""))
        return Tracked(said, ENDINGS.get(said))

    async def record(self, account: CourierAccount, phone: str) -> str | None:
        """Its fraud score: the mobile's record across Steadfast's merchants (D91). The old
        `/fraud_check/{phone}` with counts was retired on 27 Sep 2026."""
        data = await self._call(account, "GET", f"fraud_check/score/{phone}")
        data = {**data, **(data.get("data") or {})}  # seen at both levels
        delivered = data.get("delivery_ratio")  # whole percents; None when nothing has finished
        cancelled = data.get("cancellation_ratio")
        reports = int(data.get("total_reports") or 0)
        said = [f"{delivered}% delivered" if delivered is not None else "no finished parcels yet"]
        if cancelled:
            said.append(f"{cancelled}% cancelled")
        said.append(f"{reports} fraud report" + ("" if reports == 1 else "s"))
        return "Steadfast: " + ", ".join(said)

    async def _call(
        self, account: CourierAccount, method: str, path: str, **kwargs: Any
    ) -> dict[str, Any]:
        """The answer when it worked; otherwise `CourierError` with a line for the owner.
        Steadfast can answer 200 with its own `status` saying it didn't."""
        headers = {"Api-Key": account.key, "Secret-Key": account.secret}
        try:
            response = await self.client.request(
                method, f"{BASE}/{path}", headers=headers, **kwargs
            )
        except httpx.HTTPError:
            raise CourierError("Couldn't reach Steadfast. Try again in a minute.") from None
        try:
            data: dict[str, Any] = response.json()
        except ValueError:
            data = {}
        if response.status_code == 401:
            raise CourierError("Steadfast refused the keys: check the API key and secret key.")
        if response.status_code == 403:
            raise CourierError("Steadfast says the account isn't active: check its KYC in the panel.")
        if response.is_success and data.get("status", 200) == 200:
            return data
        said = data.get("message") or "; ".join(str(e) for e in data.get("errors", {}).values())
        raise CourierError(f"Steadfast answered {data.get('status', response.status_code)}. {said}")
