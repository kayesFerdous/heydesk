"""A shop's WooCommerce store (D75): its products, read every few minutes, and our orders,
written into it so its stock takes them off."""

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class WooStore:
    url: str  # "https://shop.example.com", no slash at the end
    key: str  # ck_…
    secret: str  # cs_…


class WooUnreachable(Exception):
    """The store couldn't do it: not reached, the key refused, or an order refused. The
    message is for the owner."""


class WooApi(Protocol):
    async def products(self, store: WooStore) -> list[dict[str, Any]]:
        """Every published product as Woo gives it; a variable one has its variations in
        `"variations"`, as objects."""
        ...

    async def can_write(self, store: WooStore) -> bool:
        """The key is Read/Write. Checked without writing anything."""
        ...

    async def create_order(self, store: WooStore, body: dict[str, Any]) -> int:
        """Writes an order; returns its id in Woo."""
        ...

    async def set_status(self, store: WooStore, order_id: int, status: str) -> None: ...
