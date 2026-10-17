"""WooCommerce's REST API (`/wp-json/wc/v3/`) over httpx, behind the `WooApi` port (D75).

The key and secret go as Basic auth, which Woo takes only over HTTPS, and only on the
`/wp-json/` address (not `?rest_route=`).
"""

from typing import Any

import httpx

from heydesk.ports.woo import WooStore, WooUnreachable

PER_PAGE = 100  # the most Woo gives at once


class WooRest:
    def __init__(self, timeout: float = 10.0) -> None:
        self.client = httpx.AsyncClient(timeout=timeout)

    async def products(self, store: WooStore) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        page, pages = 1, 1
        while page <= pages:
            params = {"status": "publish", "per_page": PER_PAGE, "page": page}
            response = await self._call(store, "GET", "products", params=params)
            pages = int(response.headers.get("X-WP-TotalPages", 1))
            found += response.json()
            page += 1
        for product in found:
            if product.get("type") == "variable":
                path = f"products/{product['id']}/variations"
                response = await self._call(store, "GET", path, params={"per_page": PER_PAGE})
                product["variations"] = response.json()
        return found

    async def can_write(self, store: WooStore) -> bool:
        """A status Woo doesn't know: a Read/Write key gets a 400 for it, a Read key a 401,
        and nothing is made either way."""
        response = await self._send(store, "POST", "orders", json={"status": "not-a-status"})
        return response.status_code == 400

    async def create_order(self, store: WooStore, body: dict[str, Any]) -> int:
        made = (await self._call(store, "POST", "orders", json=body)).json().get("id")
        if not isinstance(made, int):
            raise WooUnreachable("WooCommerce didn't make the order.")
        return made

    async def set_status(self, store: WooStore, order_id: int, status: str) -> None:
        await self._call(store, "PUT", f"orders/{order_id}", json={"status": status})

    async def _call(self, store: WooStore, method: str, path: str, **kwargs: Any) -> httpx.Response:
        response = await self._send(store, method, path, **kwargs)
        if response.is_success:
            return response
        if response.status_code == 401:
            raise WooUnreachable("WooCommerce refused the key: check the key and secret.")
        if response.status_code == 404:
            raise WooUnreachable("There's no WooCommerce at that address: check it.")
        try:
            said = response.json().get("message", "")
        except ValueError:
            said = ""
        raise WooUnreachable(f"WooCommerce answered {response.status_code}. {said}".strip())

    async def _send(self, store: WooStore, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            return await self.client.request(
                method,
                f"{store.url}/wp-json/wc/v3/{path}",
                auth=(store.key, store.secret),
                **kwargs,
            )
        except httpx.HTTPError:
            raise WooUnreachable("Couldn't reach your store. Try again in a minute.") from None
