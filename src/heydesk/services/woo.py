"""A shop's list kept in its WooCommerce store, and our orders written there.

The owner links the store with a Read/Write key, and we make webhooks in it with that key:
when a product or an order changes there, Woo posts to us and we sync. An order saved in
a chat is written at once, in the background, so the website can't sell the same last piece.
Every 30 minutes a safety sync runs anyway and puts back a webhook Woo turned off; with
no public address for webhooks, the store is synced every WOO_SYNC_MINUTES instead. A sync
pushes, then pulls: each order whose status changed since it was last written is created or moved in Woo,
so Woo's stock takes it off or puts it back; then Woo's products join the list, with
Woo's count less the orders not in Woo yet, and the ones deleted in Woo leave it. A problem keeps the old list, and the owner hears once.
"""

import asyncio
import base64
import contextlib
import hashlib
import hmac
import json
import logging
import secrets
import time
from collections.abc import Coroutine
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from heydesk.errors import Conflict, InvalidConfig, NotFound
from heydesk.models import SheetLink, WooLink
from heydesk.ports.woo import WooApi, WooStore, WooUnreachable
from heydesk.services.agents import AgentService
from heydesk.services.team import TeamService
from heydesk.shop.models import ShopOrder
from heydesk.shop.store import ShopStore
from heydesk.shop.woo_import import products_from_woo, woo_ids

log = logging.getLogger(__name__)

WOO_STATUS = {  # ours → Woo's. Every one but returned holds or settles Woo's stock
    "unpaid": "on-hold",
    "new": "processing",
    "on_hold": "on-hold",
    "confirmed": "processing",
    "packed": "processing",
    "shipped": "processing",
    "delivered": "completed",
    "cancelled": "cancelled",
}  # returned: left as it is in Woo; the owner restocks there, as here
READ_WRITE = "Make the key Read/Write in WooCommerce: we write your orders there."
ONE_PRODUCT_SOURCE = "Unlink the Google Sheet first: a shop's list comes from one place."
TOPICS = ("product.created", "product.updated", "product.deleted", "order.created", "order.updated")
FULL_SYNC_SECONDS = 30 * 60  # a store with webhooks is still synced this often, and they're checked
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
        # A coupon's discount, as a negative fee: Woo's own coupons are its own codes
        "fee_lines": (
            [{"name": f"Coupon {order.coupon}", "total": f"-{amount(order.discount)}"}]
            if order.discount
            else []
        ),
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
        hook_base: str = "",
    ) -> None:
        self.sessions = sessions
        self.agents = agents
        self.team = team
        self.api = api
        self.local_http = local_http  # development only: http://localhost is taken too
        # where Woo posts its webhooks: https://…/api/woo/hook. Empty: no public address, so
        # no webhooks, and stores are synced every WOO_SYNC_MINUTES
        self.hook_base = hook_base
        self.hook_delay = (
            2.0  # seconds: a burst of webhooks (an order and its products) is one sync
        )
        self.tasks: set[asyncio.Task[None]] = set()  # pushes and syncs running in the background
        self.waiting: set[str] = set()  # workspaces with a sync on its way
        self.checked: dict[str, float] = {}  # each workspace's last safety sync (monotonic)
        self.lock = asyncio.Lock()  # one push at a time, or an order could be written twice

    async def link(self, workspace_id: str, url: str, key: str, secret: str) -> WooLink:
        """Checks the key can read the products and write orders (without writing one),
        saves the link, replacing one already there, and takes the list from the store."""
        agent = await self.agents.get(workspace_id)
        url = url.strip().rstrip("/")
        local = self.local_http and urlsplit(url).hostname in LOCAL
        if not url.startswith("https://") and not local:
            raise InvalidConfig({"url": "Paste your store's address: https://…"})
        store = WooStore(url, key.strip(), secret.strip())
        async with self.sessions() as s:
            if await s.scalar(select(SheetLink.id).where(SheetLink.workspace_id == workspace_id)):
                raise Conflict(ONE_PRODUCT_SOURCE)
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
        shop = await self.agents.store(workspace_id)
        await shop.import_woo(found, set())
        hooks, hook_secret = await self._add_hooks(store, workspace_id)
        async with self.sessions() as s, s.begin():
            old = await s.scalar(select(WooLink).where(WooLink.workspace_id == workspace_id))
            if old is not None:
                dropped = (WooStore(old.url, old.key, old.secret), old.hooks)
                await s.delete(old)
                await s.flush()
            link = WooLink(
                workspace_id=workspace_id,
                agent_id=agent.id,
                url=url,
                key=store.key,
                secret=store.secret,
                ids=ids,
                checked=hash_items(items),
                synced_at=shop.now(),
                created_at=shop.now(),  # the orders placed from now on are written there
                hooks=hooks,
                hook_secret=hook_secret,
            )
            s.add(link)
        if old is not None:
            await self._drop_hooks(*dropped)
        log.info("woo linked", extra={"workspace_id": workspace_id, "webhooks": bool(hooks)})
        return link

    async def get(self, workspace_id: str) -> WooLink:
        async with self.sessions() as s:
            link = await s.scalar(select(WooLink).where(WooLink.workspace_id == workspace_id))
        if link is None:
            raise NotFound("No WooCommerce store is linked.")
        return link

    async def unlink(self, workspace_id: str) -> None:
        """Stops syncing. The list stays and can be edited here again; orders already in Woo
        stay there."""
        async with self.sessions() as s, s.begin():
            link = await s.scalar(select(WooLink).where(WooLink.workspace_id == workspace_id))
            if link is not None:
                await s.delete(link)
        if link is not None:
            await self._drop_hooks(WooStore(link.url, link.key, link.secret), link.hooks)

    async def sync(self, workspace_id: str) -> WooLink:
        """Pushes the orders that changed, then pulls the products. Never raises for the
        store's own problems: they're `problem`."""
        link = await self.get(workspace_id)
        shop = await self.agents.store(workspace_id)
        store = WooStore(link.url, link.key, link.secret)
        async with self.lock:
            problem = await self._push(shop, store, link)
        ids, checked = link.ids, link.checked
        try:
            items = await self.api.products(store)
            if hash_items(items) != checked:  # the same as last time: nothing to do
                found, ids = products_from_woo(items)
                await shop.import_woo(found, set(link.ids) - set(ids))  # deleted in Woo
                checked = hash_items(items)
        except (WooUnreachable, ValueError) as error:  # the old list stays
            problem = problem or str(error)
        async with self.sessions() as s, s.begin():
            saved = await s.get_one(WooLink, link.id)
            told = saved.problem
            saved.problem, saved.ids, saved.checked = problem, ids, checked
            if not problem:
                saved.synced_at = shop.now()
        if problem and problem != told:
            await self.team.notify(workspace_id, f"⚠️ Your WooCommerce store: {problem}")
        log.info("woo synced", extra={"workspace_id": workspace_id, "problem": bool(problem)})
        return saved

    def push_soon(self, workspace_id: str) -> None:
        """Writes the shop's changed orders into its store now, without waiting for it (a chat
        turn calls this). A problem is left for the next sync, which tries again and tells the
        owner."""
        self._run_in_background(self.push(workspace_id))

    async def push(self, workspace_id: str) -> None:
        async with self.sessions() as s:
            link = await s.scalar(select(WooLink).where(WooLink.workspace_id == workspace_id))
        if link is None:  # not linked: nothing to write
            return
        try:
            async with self.lock:
                await self._push(
                    await self.agents.store(workspace_id),
                    WooStore(link.url, link.key, link.secret),
                    link,
                )
        except Exception:
            log.exception("woo push failed", extra={"workspace_id": workspace_id})

    async def hook_signed(self, workspace_id: str, body: bytes, signature: str) -> bool:
        """Woo signs each webhook post: base64 of its HMAC-SHA256 with the secret we gave it."""
        async with self.sessions() as s:
            link = await s.scalar(select(WooLink).where(WooLink.workspace_id == workspace_id))
        if link is None or not link.hook_secret:
            return False
        digest = hmac.new(link.hook_secret.encode(), body, hashlib.sha256).digest()
        return hmac.compare_digest(base64.b64encode(digest).decode(), signature)

    def sync_soon(self, workspace_id: str) -> None:
        """A webhook said something changed in the store: sync in a moment, without making Woo
        wait. The webhooks that come in the meantime join that one sync."""
        if workspace_id in self.waiting:
            return
        self.waiting.add(workspace_id)
        self._run_in_background(self._sync_after_delay(workspace_id))

    async def _sync_after_delay(self, workspace_id: str) -> None:
        await asyncio.sleep(self.hook_delay)
        self.waiting.discard(workspace_id)
        try:
            await self.sync(workspace_id)
        except Exception:
            log.exception("woo sync failed", extra={"workspace_id": workspace_id})

    async def sync_all(self) -> None:
        """Syncs each store without webhooks, and each with them once every FULL_SYNC_SECONDS
        (a background job), checking its webhooks too: a store linked without them (linked
        before webhooks were added, or with no public address then) gets them now. One store
        failing doesn't stop the others."""
        async with self.sessions() as s:
            linked = list(await s.execute(select(WooLink.workspace_id, WooLink.hooks)))
        for workspace_id, hooks in linked:
            last = self.checked.get(workspace_id)
            if hooks and last is not None and time.monotonic() - last < FULL_SYNC_SECONDS:
                continue  # its webhooks keep it up to date
            self.checked[workspace_id] = time.monotonic()
            try:
                await self.sync(workspace_id)
                await self.keep_hooks(workspace_id)
            except Exception:
                log.exception("woo sync failed", extra={"workspace_id": workspace_id})

    async def keep_hooks(self, workspace_id: str) -> None:
        """Puts back our webhooks as they should be: one Woo turned off after failed posts is
        turned on, one pointing at an old address (a new tunnel) is pointed here, and one
        deleted in Woo's admin is made again; a link with none gets them all. Nothing to do
        with no public address."""
        if not self.hook_base:
            return
        link = await self.get(workspace_id)
        store = WooStore(link.url, link.key, link.secret)
        url = f"{self.hook_base}/{workspace_id}"
        hooks = dict(link.hooks)
        secret = link.hook_secret or secrets.token_urlsafe(32)
        try:
            for topic in TOPICS:
                found = await self.api.webhook(store, hooks[topic]) if topic in hooks else None
                if found is None:
                    hooks[topic] = await self.api.add_webhook(store, topic, url, secret)
                elif found.get("status") != "active" or found.get("delivery_url") != url:
                    changes = {"status": "active", "delivery_url": url}
                    await self.api.update_webhook(store, hooks[topic], changes)
                    log.info(
                        "woo webhook put back", extra={"workspace_id": workspace_id, "topic": topic}
                    )
        except WooUnreachable:  # the sync tells the owner when the store can't be reached
            return
        finally:
            if hooks != link.hooks:
                async with self.sessions() as s, s.begin():
                    saved = await s.get_one(WooLink, link.id)
                    saved.hooks, saved.hook_secret = hooks, secret  # a new dict, so it's seen

    async def _add_hooks(
        self, store: WooStore, workspace_id: str
    ) -> tuple[dict[str, int], str | None]:
        """Makes our webhooks in the store. With no public address, or a store that won't
        make them, there are none and the store is synced every few minutes instead."""
        if not self.hook_base:
            return {}, None
        secret = secrets.token_urlsafe(32)
        hooks: dict[str, int] = {}
        try:
            for topic in TOPICS:
                hooks[topic] = await self.api.add_webhook(
                    store, topic, f"{self.hook_base}/{workspace_id}", secret
                )
        except WooUnreachable:
            log.warning("woo webhooks not made", extra={"workspace_id": workspace_id})
            await self._drop_hooks(store, hooks)
            return {}, None
        return hooks, secret

    async def _drop_hooks(self, store: WooStore, hooks: dict[str, int]) -> None:
        """Deletes our webhooks from the store. One it won't delete is left: it posts to a
        workspace with no link, which gets a 404, and Woo turns it off after 5."""
        for webhook_id in hooks.values():
            with contextlib.suppress(WooUnreachable):
                await self.api.delete_webhook(store, webhook_id)

    def _run_in_background(self, work: Coroutine[Any, Any, None]) -> None:
        """Runs `work` in the background, kept so it isn't dropped halfway."""
        task = asyncio.create_task(work)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def _push(self, shop: ShopStore, store: WooStore, link: WooLink) -> str:
        """Writes each order that changed. Stops at the first that can't be written, which
        is tried again next sync; returns its problem ("" when all went)."""
        for order in await shop.orders_for_woo(link.created_at):
            status = WOO_STATUS.get(order.status)
            try:
                if order.woo_id is None and status not in (None, "cancelled"):
                    woo_id = await self.api.create_order(store, woo_order(order, link.ids))
                    await shop.mark_sent_to_woo(order.id, woo_id, order.status)
                    continue
                if order.woo_id is not None and status and status != WOO_STATUS.get(order.woo_sent):
                    await self.api.set_status(store, order.woo_id, status)
            except KeyError:
                return (
                    f"{order.code} wasn't written: one of its products isn't in the store any more."
                )
            except WooUnreachable as error:
                return f"{order.code} wasn't written: {error}"
            await shop.mark_sent_to_woo(
                order.id, order.woo_id, order.status
            )  # nothing else to write
        return ""


def hash_items(items: list[dict[str, Any]]) -> str:
    return hashlib.sha256(json.dumps(items, sort_keys=True).encode()).hexdigest()
