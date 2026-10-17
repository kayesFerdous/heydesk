"""A shop's list kept in its WooCommerce store, and our orders written there (D75).

The owner links the store with a Read/Write key. An order saved in a chat is written at once,
in the background, so the website can't sell the same last piece (D77). And every few minutes
a sync pushes, then pulls: each order whose status changed since it was last written is created or moved in Woo,
so Woo's stock takes it off or puts it back; then Woo's products become the list, with Woo's
count less the orders not in Woo yet. A problem keeps the old list, and the owner hears once.
"""

import asyncio
import hashlib
import json
import logging
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from heydesk.desks.shop.models import ShopOrder
from heydesk.desks.shop.store import ShopStore
from heydesk.desks.shop.values import products_from_woo, woo_ids
from heydesk.errors import Conflict, InvalidConfig, NotFound
from heydesk.models import SheetLink, WooLink
from heydesk.ports.woo import WooApi, WooStore, WooUnreachable
from heydesk.services.agents import AgentService
from heydesk.services.team import TeamService

log = logging.getLogger(__name__)

WOO_STATUS = {  # ours → Woo's. Every one but returned holds or settles Woo's stock
    "unpaid": "on-hold",
    "new": "processing",
    "confirmed": "processing",
    "shipped": "processing",
    "delivered": "completed",
    "cancelled": "cancelled",
}  # returned: left as it is in Woo; the owner restocks there, as here
READ_WRITE = "Make the key Read/Write in WooCommerce: we write your orders there."
ONE_LIST = "Unlink the Google Sheet first: a shop's list comes from one place."
LOCAL = ("localhost", "127.0.0.1")  # in development, the test shop in dev/woo-shop over plain HTTP


def amount(minor: int) -> str:
    """Minor units as Woo writes money: 245000 → "2450.00"."""
    return f"{minor // 100}.{minor % 100:02d}"


def woo_order(order: ShopOrder, ids: dict[str, Any]) -> dict[str, Any]:
    """The order as Woo takes it, with our prices: what the buyer said yes to (rule 1). Raises
    KeyError when one of its products isn't in the store any more."""
    lines = []
    for line in order.lines:
        product_id, variation_id = woo_ids(ids, line)
        total = amount(line["price"] * line["quantity"])
        item = {"product_id": product_id, "quantity": line["quantity"]}
        item |= {"subtotal": total, "total": total}
        if variation_id is not None:
            item["variation_id"] = variation_id
        lines.append(item)
    buyer = {
        "first_name": order.name,
        "phone": order.phone,
        "address_1": order.address,
        "city": order.zone,
    }
    meta = [{"key": "heydesk_code", "value": order.code}]
    if order.paid:
        meta.append(
            {"key": "heydesk_paid_online", "value": f"{amount(order.paid)} ({order.payment_ref})"}
        )
    return {
        "status": WOO_STATUS[order.status],
        "payment_method": "cod",
        "payment_method_title": "Cash on delivery",
        "set_paid": False,
        "billing": buyer,
        "shipping": buyer,
        "customer_note": order.note,
        "line_items": lines,
        "shipping_lines": [
            {
                "method_id": "flat_rate",
                "method_title": f"Delivery: {order.zone}",
                "total": amount(order.delivery_charge),
            }
        ],
        "meta_data": meta,
    }


class WooService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        agents: AgentService,
        team: TeamService,
        api: WooApi,
        local_http: bool = False,
    ) -> None:
        self.sessions = sessions
        self.agents = agents
        self.team = team
        self.api = api
        self.local_http = local_http  # development only: http://localhost is taken too
        self.pushing: set[asyncio.Task[None]] = set()  # pushes running now; kept so they finish
        self.lock = asyncio.Lock()  # one push at a time, or an order could be written twice

    async def link(self, ws: str, url: str, key: str, secret: str) -> WooLink:
        """Checks the key can read the products and write orders (without writing one),
        saves the link, replacing one already there, and takes the list from the store."""
        agent = await self.agents.get(ws)
        if agent.kind != "shop":
            raise NotFound("Only a shop can link a WooCommerce store.")
        url = url.strip().rstrip("/")
        local = self.local_http and urlsplit(url).hostname in LOCAL
        if not url.startswith("https://") and not local:
            raise InvalidConfig({"url": "Paste your store's address: https://…"})
        store = WooStore(url, key.strip(), secret.strip())
        async with self.sessions() as s:
            if await s.scalar(select(SheetLink.id).where(SheetLink.workspace_id == ws)):
                raise Conflict(ONE_LIST)
        try:
            items = await self.api.products(store)
            if not await self.api.can_write(store):
                raise InvalidConfig({"key": READ_WRITE})
        except WooUnreachable as error:
            raise InvalidConfig({"key": str(error)}) from None
        try:
            found, ids = products_from_woo(items)
        except ValueError as error:
            raise InvalidConfig({"products": str(error)}) from None
        shop = await self._store(ws)
        await shop.take_woo(found)
        async with self.sessions() as s, s.begin():
            old = await s.scalar(select(WooLink).where(WooLink.workspace_id == ws))
            if old is not None:
                await s.delete(old)
                await s.flush()
            link = WooLink(
                workspace_id=ws,
                agent_id=agent.id,
                url=url,
                key=store.key,
                secret=store.secret,
                ids=ids,
                checked=digest(items),
                synced_at=shop.now(),
                created_at=shop.now(),  # the orders placed from now on are written there
            )
            s.add(link)
        log.info("woo linked", extra={"workspace_id": ws})
        return link

    async def get(self, ws: str) -> WooLink:
        async with self.sessions() as s:
            link = await s.scalar(select(WooLink).where(WooLink.workspace_id == ws))
        if link is None:
            raise NotFound("No WooCommerce store is linked.")
        return link

    async def unlink(self, ws: str) -> None:
        """Stops syncing. The list stays and can be edited here again; orders already in Woo
        stay there."""
        async with self.sessions() as s, s.begin():
            link = await s.scalar(select(WooLink).where(WooLink.workspace_id == ws))
            if link is not None:
                await s.delete(link)

    async def sync(self, ws: str) -> WooLink:
        """Pushes the orders that changed, then pulls the products. Never raises for the
        store's own problems: they're `problem`."""
        link = await self.get(ws)
        shop = await self._store(ws)
        store = WooStore(link.url, link.key, link.secret)
        async with self.lock:
            problem = await self._push(shop, store, link)
        ids, checked = link.ids, link.checked
        try:
            items = await self.api.products(store)
            if digest(items) != checked:  # the same as last time: nothing to do
                found, ids = products_from_woo(items)
                await shop.take_woo(found)
                checked = digest(items)
        except (WooUnreachable, ValueError) as error:  # the old list stays
            problem = problem or str(error)
        async with self.sessions() as s, s.begin():
            saved = await s.get_one(WooLink, link.id)
            told = saved.problem
            saved.problem, saved.ids, saved.checked = problem, ids, checked
            if not problem:
                saved.synced_at = shop.now()
        if problem and problem != told:
            await self.team.notify(ws, f"⚠️ Your WooCommerce store: {problem}")
        log.info("woo synced", extra={"workspace_id": ws, "problem": bool(problem)})
        return saved

    def push_soon(self, ws: str) -> None:
        """Writes the shop's changed orders into its store now, without waiting for it (a chat
        turn calls this). A problem is left for the next sync, which tries again and tells the
        owner."""
        task = asyncio.create_task(self.push(ws))
        self.pushing.add(task)
        task.add_done_callback(self.pushing.discard)

    async def push(self, ws: str) -> None:
        async with self.sessions() as s:
            link = await s.scalar(select(WooLink).where(WooLink.workspace_id == ws))
        if link is None:  # not linked: nothing to write
            return
        try:
            async with self.lock:
                await self._push(
                    await self._store(ws), WooStore(link.url, link.key, link.secret), link
                )
        except Exception:
            log.exception("woo push failed", extra={"workspace_id": ws})

    async def run(self, every: float) -> None:
        """Syncs every linked store, one after another, every `every` seconds, until
        cancelled (the app's lifespan starts it). One store failing doesn't stop the others."""
        while True:
            await asyncio.sleep(every)
            async with self.sessions() as s:
                linked = list(await s.scalars(select(WooLink.workspace_id)))
            for ws in linked:
                try:
                    await self.sync(ws)
                except Exception:
                    log.exception("woo sync failed", extra={"workspace_id": ws})

    async def _push(self, shop: ShopStore, store: WooStore, link: WooLink) -> str:
        """Writes each order that changed. Stops at the first that can't be written, which
        is tried again next sync; returns its problem ("" when all went)."""
        for order in await shop.orders_for_woo(link.created_at):
            status = WOO_STATUS.get(order.status)
            try:
                if order.woo_id is None and status not in (None, "cancelled"):
                    woo_id = await self.api.create_order(store, woo_order(order, link.ids))
                    await shop.woo_written(order.id, woo_id, order.status)
                    continue
                if order.woo_id is not None and status and status != WOO_STATUS.get(order.woo_sent):
                    await self.api.set_status(store, order.woo_id, status)
            except KeyError:
                return (
                    f"{order.code} wasn't written: one of its products isn't in the store any more."
                )
            except WooUnreachable as error:
                return f"{order.code} wasn't written: {error}"
            await shop.woo_written(order.id, order.woo_id, order.status)  # nothing else to write
        return ""

    async def _store(self, ws: str) -> ShopStore:
        store = await self.agents.store(ws)
        assert isinstance(store, ShopStore)
        return store


def digest(items: list[dict[str, Any]]) -> str:
    return hashlib.sha256(json.dumps(items, sort_keys=True).encode()).hexdigest()
