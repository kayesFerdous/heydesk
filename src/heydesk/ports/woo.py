"""A shop's WooCommerce store: its products, read when its webhooks say something
changed, and our orders, written into it so its stock takes them off."""

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class WooCredentials:
    url: str  # "https://shop.example.com", no slash at the end
    key: str  # ck_…
    secret: str  # cs_…


class WooUnreachable(Exception):
    """The store couldn't do it: not reached, the key refused, or an order refused. The
    message is for the owner."""


class WooApi(Protocol):
    async def products(self, store: WooCredentials) -> list[dict[str, Any]]:
        """Every published product as Woo gives it; a variable one has its variations in
        `"variations"`, as objects."""
        ...

    async def can_write(self, store: WooCredentials) -> bool:
        """The key is Read/Write. Checked without writing anything."""
        ...

    async def create_order(self, store: WooCredentials, body: dict[str, Any]) -> int:
        """Writes an order; returns its id in Woo."""
        ...

    async def set_status(self, store: WooCredentials, order_id: int, status: str) -> None: ...

    async def add_webhook(self, store: WooCredentials, topic: str, url: str, secret: str) -> int:
        """Asks Woo to post to `url` when `topic` happens ("product.updated"), signed with
        `secret`; returns the webhook's id."""
        ...

    async def webhook(self, store: WooCredentials, webhook_id: int) -> dict[str, Any] | None:
        """The webhook as Woo gives it (its `status`: "active", or "disabled" after 5 failed
        posts; its `delivery_url`), or None when it's gone."""
        ...

    async def update_webhook(
        self, store: WooCredentials, webhook_id: int, changes: dict[str, Any]
    ) -> None: ...

    async def delete_webhook(self, store: WooCredentials, webhook_id: int) -> None:
        """Gone already is fine."""
        ...
