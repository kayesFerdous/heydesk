"""A shop's data: its products and orders, plus the rest (`Store`).

Placing an order takes its items out of stock in the same transaction, and cancelling it puts
them back. Every write to the stock takes the business's lock first (`Store._lock`), so two
buyers at the same moment can't both get the last one."""

from dataclasses import replace
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import ScalarResult, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from heydesk.domain.values import Confirmed
from heydesk.errors import Conflict, InvalidTransition, NotFound
from heydesk.models import Conversation, Turn
from heydesk.models.customer_request import next_number
from heydesk.shop.base import PAGE, Store, TooLate
from heydesk.shop.models import CouponRow, ProductRow, ShopOrder
from heydesk.shop.values import (
    CHANGEABLE,
    ENDED,
    HOLDS,
    OWN,
    Buyer,
    BuyerRecord,
    Coupon,
    NewOrder,
    OrderCancel,
    OrderConfirm,
    OrderEdit,
    Product,
    added,
    merge,
    products_from_csv,
    stock_snapshot,
    take_off,
)

TO_BOOK = ("confirmed", "packed")  # the courier books these; not one on hold


class CouponGone(ValueError):
    """The coupon ended or was used up since the cart was made."""


class OutOfStock(ValueError):
    """Someone else bought the last ones since the cart was made."""


class ShopStore(Store):
    SALES = (ShopOrder,)

    def order_link(self, order_id: str) -> str:
        """The order's page for the buyer, `/o/<id>`; the id is long and random."""
        return f"{self.public_url}/o/{order_id}"

    async def products(self) -> list[Product]:
        return await self.rows_of(ProductRow, Product)

    async def replace_products(self, products: list[Product]) -> None:
        await self.replace_rows(ProductRow, products)

    async def add_products(self, found: list[Product]) -> int:
        """A file's products join the list: the ones we have are updated, the rest are
        added, and none is deleted. Returns how many were new."""
        found = await self.with_photos(found)  # downloads the new links
        async with self.sessions() as s, s.begin():
            await self._lock(s)
            ours = await self._products(s)
            now = added(ours, found)
            await self._replace(s, ProductRow, now)
        return len(now) - len(ours)

    async def _products(self, s: AsyncSession) -> list[Product]:
        rows = await s.scalars(
            select(ProductRow)
            .where(ProductRow.workspace_id == self.workspace_id)
            .order_by(ProductRow.position)
        )
        return [Product.model_validate(r, from_attributes=True) for r in rows]

    async def with_photos(self, found: list[Product]) -> list[Product]:
        """A list's products with their photos: a product with photo links gets those
        photos, each downloaded and indexed the first time; one with none keeps the photos it
        has here (the owner's own uploads)."""
        # ponytail: a first import with many new links waits for them all (4 at a time);
        # move this to the background if owners wait too long
        ours = {p.code: p.photos for p in await self.products()}
        wanted = [link for p in found for link in p.photos]
        taken = await self.photos.take(self.workspace_id, wanted) if self.photos and wanted else {}
        return [
            p.model_copy(
                update={
                    "photos": [taken[link] for link in p.photos if link in taken]
                    if p.photos
                    else ours.get(p.code, [])
                }
            )
            for p in found
        ]

    async def take_sheet(
        self, text: str, mapping: dict[str, str], seen: dict[str, Any]
    ) -> dict[str, Any]:
        """The sheet's products, keeping our count where the sheet's didn't change, and
        taking a new count less the orders not shipped yet, and its photos. They
        join the list: the ones the sheet had last time (`seen`) and hasn't now go."""
        found = await self.with_photos(products_from_csv(text, mapping))
        ws = self.workspace_id
        async with self.sessions() as s, s.begin():
            await self._lock(s)  # an order placed meanwhile is counted, not lost
            listed = await self._products(s)
            ours = {p.code: p for p in listed}
            orders: ScalarResult[list[dict[str, Any]]] = await s.scalars(
                select(ShopOrder.lines).where(
                    ShopOrder.workspace_id == ws, ShopOrder.status.in_(HOLDS)
                )
            )
            held = [line for lines in orders for line in lines]
            gone = set(seen) - {p.code for p in found}
            await self._replace(s, ProductRow, added(listed, merge(found, ours, seen, held), gone))
        return {p.code: stock_snapshot(p) for p in found}

    async def take_woo(self, found: list[Product], gone: set[str]) -> None:
        """The WooCommerce store's products join the list, with Woo's count less
        the orders that hold stock here but aren't in Woo yet, and Woo's photos. The
        `gone` codes, ones Woo had last time and hasn't now, leave it."""
        found = await self.with_photos(found)
        ws = self.workspace_id
        async with self.sessions() as s, s.begin():
            await self._lock(s)  # an order placed meanwhile is counted, not lost
            orders: ScalarResult[list[dict[str, Any]]] = await s.scalars(
                select(ShopOrder.lines).where(
                    ShopOrder.workspace_id == ws,
                    ShopOrder.status.in_(HOLDS),
                    ShopOrder.woo_id.is_(None),
                )
            )
            held = [line for lines in orders for line in lines]
            kept = [take_off(p, [line for line in held if line["code"] == p.code]) for p in found]
            await self._replace(s, ProductRow, added(await self._products(s), kept, gone))

    async def orders_for_woo(self, since: datetime) -> list[ShopOrder]:
        """The orders placed since the store was linked whose status changed since it was
        last written there, oldest first."""
        async with self.sessions() as s:
            rows = await s.scalars(
                select(ShopOrder)
                .where(
                    ShopOrder.workspace_id == self.workspace_id,
                    ShopOrder.created_at >= since,
                    ShopOrder.status != ShopOrder.woo_sent,
                )
                .order_by(ShopOrder.created_at)
            )
            return list(rows)

    async def woo_written(self, order_id: str, woo_id: int | None, status: str) -> None:
        """The order is in Woo as `status` (ours) now."""
        async with self.sessions() as s, s.begin():
            order = await self._one(s, ShopOrder, order_id)
            order.woo_id, order.woo_sent = woo_id, status

    async def orders_for_courier(self, status: str) -> list[ShopOrder]:
        """The orders the courier deals with, oldest first: `confirmed` (or `packed`)
        ones with no parcel yet, to book; `shipped` ones with a parcel, to follow."""
        booked = ShopOrder.parcel_id == "" if status == "confirmed" else ShopOrder.parcel_id != ""
        statuses = TO_BOOK if status == "confirmed" else (status,)
        async with self.sessions() as s:
            rows = await s.scalars(
                select(ShopOrder)
                .where(
                    ShopOrder.workspace_id == self.workspace_id,
                    ShopOrder.status.in_(statuses),
                    booked,
                )
                .order_by(ShopOrder.created_at)
            )
            return list(rows)

    async def to_review(self, delivered_before: datetime) -> list[ShopOrder]:
        """Delivered orders from a chat whose buyer wasn't asked how it was yet."""
        async with self.sessions() as s:
            rows = await s.scalars(
                select(ShopOrder).where(
                    ShopOrder.workspace_id == self.workspace_id,
                    ShopOrder.status == "delivered",
                    ShopOrder.review_asked.is_(False),
                    ShopOrder.conversation_id.is_not(None),
                    ShopOrder.updated_at <= delivered_before,
                )
            )
            return list(rows)

    async def mark_notified(self, order_id: str, what: Literal["reminded", "review_asked"]) -> None:
        """The buyer was told this once, so the next round doesn't tell them again."""
        async with self.sessions() as s, s.begin():
            setattr(await self._one(s, ShopOrder, order_id), what, True)

    async def parcel_booked(self, order_id: str, parcel_id: str) -> ShopOrder:
        """The courier has the order's parcel: it's `shipped`, unless it moved meanwhile."""
        async with self.sessions() as s, s.begin():
            order = await self._one(s, ShopOrder, order_id)
            order.parcel_id = parcel_id
            if order.status in TO_BOOK:
                self._move(order, "shipped")
        return order

    async def courier_said(self, order_id: str, said: str, ending: str | None) -> ShopOrder:
        """The courier's words about the parcel, and its end: delivered or returned."""
        async with self.sessions() as s, s.begin():
            order = await self._one(s, ShopOrder, order_id)
            order.courier_status = said[:100]
            if ending and order.status == "shipped":
                self._move(order, ending)
        return order

    async def place_order(
        self, order: Confirmed[NewOrder], source: Literal["chat", "page"] = "chat"
    ) -> ShopOrder:
        """Takes the items out of stock and saves the order as its status says: `unpaid` when
        its delivery charge is paid first, `new` for the shop's call, or `confirmed`.
        Raises OutOfStock, and saves nothing, if there aren't enough left."""
        return await self._save_order(order.value, source)

    async def add_team_order(self, order: NewOrder) -> ShopOrder:
        """An order the team typed for a buyer who called: no read-back to a visitor
        and no `Confirmed`, as the team member is the yes. Saved `confirmed`, in no chat."""
        return await self._save_order(replace(order, status="confirmed"), "team")

    async def _save_order(self, o: NewOrder, source: str) -> ShopOrder:
        """Raises OutOfStock or CouponGone, and saves nothing."""
        async with self.sessions() as s, s.begin():
            await self._lock(s)  # one save at a time: the last one and a coupon's last use
            await self._stock(s, o.lines, -1)
            if o.coupon:  # one more use, checked again in this transaction
                row = await s.scalar(
                    select(CouponRow).where(
                        CouponRow.workspace_id == self.workspace_id, CouponRow.code == o.coupon
                    )
                )
                problem = (
                    Coupon.model_validate(row, from_attributes=True).problem(self.now())
                    if row and self._may_use(row)
                    else f"The coupon {o.coupon} is gone."
                )
                if row is None or problem:
                    raise CouponGone(problem)
                row.used += 1
            now = self.now()
            saved = ShopOrder(
                workspace_id=self.workspace_id,
                conversation_id=self.conversation_id,
                number=await next_number(s, ShopOrder, self.workspace_id),
                created_at=now,
                updated_at=now,
                status=o.status,
                name=o.name,
                phone=o.phone,
                lines=o.lines,
                subtotal=o.subtotal,
                delivery_charge=o.delivery_charge,
                discount=o.discount,
                coupon=o.coupon,
                total=o.total,
                currency=o.currency,
                zone=o.zone,
                address=o.address,
                note=o.note,
                source=source,
            )
            s.add(saved)
        return saved

    async def same_order(
        self, phone: str, lines: list[dict[str, Any]], since: datetime
    ) -> ShopOrder | None:
        """The newest order from this mobile since then, not cancelled, with the same items:
        a second one is usually a mistake or a prank, and costs a delivery."""

        def items(of: list[dict[str, Any]]) -> set[tuple[str, str, str]]:
            return {(line["code"], line.get("size", ""), line.get("colour", "")) for line in of}

        async with self.sessions() as s:
            found = await s.scalars(
                select(ShopOrder)
                .where(
                    ShopOrder.workspace_id == self.workspace_id,
                    ShopOrder.phone == phone,
                    ShopOrder.created_at >= since,
                    ShopOrder.status != "cancelled",
                )
                .order_by(ShopOrder.created_at.desc())
            )
            return next((o for o in found if items(o.lines) == items(lines)), None)

    async def return_history(self, phone: str) -> dict[str, int]:
        """How many of this mobile's orders came back or were cancelled: {"returned": 2}. For
        the owner's new-order alert, since a fake order costs a shop two delivery charges."""
        async with self.sessions() as s:
            rows = await s.execute(
                select(ShopOrder.status, func.count())
                .where(
                    ShopOrder.workspace_id == self.workspace_id,
                    ShopOrder.phone == phone,
                    ShopOrder.status.in_(["returned", "cancelled"]),
                )
                .group_by(ShopOrder.status)
            )
            return {status: count for status, count in rows}

    async def coupon(self, code: str) -> Coupon | None:
        """The code, if this chat may use it: an offer's only by the chats it went to."""
        async with self.sessions() as s:
            row = await s.scalar(
                select(CouponRow).where(
                    CouponRow.workspace_id == self.workspace_id, CouponRow.code == code.upper()
                )
            )
        if row is None or not self._may_use(row):
            return None
        return Coupon.model_validate(row, from_attributes=True)

    def _may_use(self, row: CouponRow) -> bool:
        return row.only_for is None or self.conversation_id in row.only_for

    async def offer_code(self) -> str:
        """The newest offer sent to this chat that hasn't ended, or ""."""
        if self.conversation_id is None:
            return ""
        async with self.sessions() as s:
            rows = await s.scalars(
                select(CouponRow)
                .where(
                    CouponRow.workspace_id == self.workspace_id,
                    CouponRow.only_for.is_not(None),
                    CouponRow.ends_at > self.now(),
                )
                .order_by(CouponRow.ends_at.desc())
            )
            return next((r.code for r in rows if self._may_use(r)), "")

    async def chats_since(self, since: datetime) -> list[str]:
        """The shop's chats where the visitor wrote since then (an offer's audience), not
        calls and not the team's own asks."""
        async with self.sessions() as s:
            found = await s.scalars(
                select(Turn.conversation_id)
                .where(
                    Turn.workspace_id == self.workspace_id,
                    Turn.at >= since,
                    Turn.channel.not_in(("voice", "assist")),
                )
                .distinct()
            )
            return list(found)

    async def add_offer(self, coupon: Coupon, chats: list[str]) -> None:
        """An offer as a coupon only `chats` may use."""
        async with self.sessions() as s, s.begin():
            fields = coupon.model_dump(exclude={"used"})
            s.add(CouponRow(workspace_id=self.workspace_id, only_for=chats, **fields))

    async def offers_made(self) -> int:
        async with self.sessions() as s:
            n = await s.scalar(
                select(func.count()).where(
                    CouponRow.workspace_id == self.workspace_id, CouponRow.only_for.is_not(None)
                )
            )
            return n or 0

    async def coupons(self) -> list[Coupon]:
        async with self.sessions() as s:
            rows = await s.scalars(
                select(CouponRow)
                .where(CouponRow.workspace_id == self.workspace_id)
                .order_by(CouponRow.code)
            )
            return [Coupon.model_validate(r, from_attributes=True) for r in rows]

    async def add_coupon(self, coupon: Coupon) -> Coupon:
        """A new code, unused. Raises Conflict if the shop has it already."""
        if await self.coupon(coupon.code):
            raise Conflict(f"There's a coupon {coupon.code} already.")
        async with self.sessions() as s, s.begin():
            s.add(CouponRow(workspace_id=self.workspace_id, **coupon.model_dump(exclude={"used"})))
        return coupon.model_copy(update={"used": 0})

    async def delete_coupon(self, code: str) -> None:
        """Orders placed with it keep their discount."""
        async with self.sessions() as s, s.begin():
            row = await s.scalar(
                select(CouponRow).where(
                    CouponRow.workspace_id == self.workspace_id, CouponRow.code == code.upper()
                )
            )
            if row is None:
                raise NotFound("There's no such coupon.")
            await s.delete(row)

    async def records(self, phones: set[str]) -> dict[str, BuyerRecord]:
        """Each mobile's orders with this shop by how they ended, in one query."""
        found: dict[str, BuyerRecord] = {phone: BuyerRecord() for phone in phones}
        if not phones:
            return found
        async with self.sessions() as s:
            rows = await s.execute(
                select(ShopOrder.phone, ShopOrder.status, func.count())
                .where(ShopOrder.workspace_id == self.workspace_id, ShopOrder.phone.in_(phones))
                .group_by(ShopOrder.phone, ShopOrder.status)
            )
            for phone, status, count in rows:
                record = found[phone]
                record.orders += count
                if status in ("delivered", "returned", "cancelled"):
                    setattr(record, status, getattr(record, status) + count)
        return found

    async def buyers(self, q: str | None = None, limit: int = PAGE) -> list[Buyer]:
        """The shop's buyers by mobile, the latest to order first. `q` matches the
        mobile or a name they ordered under."""
        ws = self.workspace_id
        query = (
            select(
                ShopOrder.phone,
                func.max(ShopOrder.created_at),
                func.sum(case((ShopOrder.status == "delivered", ShopOrder.total), else_=0)),
            )
            .where(ShopOrder.workspace_id == ws)
            .group_by(ShopOrder.phone)
        )
        if q := (q or "").strip():
            like = f"%{q}%"
            query = query.where(or_(ShopOrder.phone.like(like), ShopOrder.name.ilike(like)))
        async with self.sessions() as s:
            top = (
                await s.execute(query.order_by(func.max(ShopOrder.number).desc()).limit(limit))
            ).all()
            phones = {phone for phone, _, _ in top}
            names = dict(
                (
                    await s.execute(
                        select(ShopOrder.phone, ShopOrder.name)
                        .where(ShopOrder.workspace_id == ws, ShopOrder.phone.in_(phones))
                        .order_by(ShopOrder.number)  # the latest wins in the dict
                    )
                ).all()
            )
            chats = (
                await s.execute(
                    select(ShopOrder.phone, Conversation.channel, Conversation.id)
                    .join(Conversation, Conversation.id == ShopOrder.conversation_id)
                    .where(ShopOrder.workspace_id == ws, ShopOrder.phone.in_(phones))
                    .distinct()
                )
            ).all()
        records = await self.records(phones)
        return [
            Buyer(
                **records[phone].model_dump(),
                phone=phone,
                name=names[phone],
                spent=spent or 0,
                last_order_at=last,
                channels=sorted({channel for p, channel, _ in chats if p == phone}),
                conversations=len({c for p, _, c in chats if p == phone}),
            )
            for phone, last, spent in top
        ]

    async def orders_here(self) -> list[ShopOrder]:
        """The orders placed in this conversation, newest first."""
        return await self.records_in_conversation(ShopOrder)

    async def latest_order(self, phone: str) -> ShopOrder | None:
        """The newest order placed with this mobile: buyers know their number, not the
        order's code."""
        async with self.sessions() as s:
            return await s.scalar(
                select(ShopOrder)
                .where(ShopOrder.workspace_id == self.workspace_id, ShopOrder.phone == phone)
                .order_by(ShopOrder.created_at.desc(), ShopOrder.number.desc())
                .limit(1)
            )

    async def change_order(self, edit: Confirmed[OrderEdit]) -> ShopOrder:
        """New items (their stock swapped: the old back, the new out) or delivery details,
        and the total again. Raises TooLate if the courier has it by now, and OutOfStock, with
        nothing changed, if there aren't enough of the new items."""
        e = edit.value
        async with self.sessions() as s, s.begin():
            await self._lock(s)
            order = await s.scalar(
                select(ShopOrder).where(
                    ShopOrder.workspace_id == self.workspace_id, ShopOrder.number == e.number
                )
            )
            if order is None:
                raise NotFound("There's no such order.")
            if order.status not in CHANGEABLE:
                raise TooLate(order.status)
            if e.lines != order.lines:
                await self._stock(s, order.lines, +1)
                await self._stock(s, e.lines, -1)
                order.lines, order.subtotal = e.lines, e.subtotal
            order.address, order.zone, order.phone, order.note = e.address, e.zone, e.phone, e.note
            order.delivery_charge = e.delivery_charge
            # ponytail: the coupon's amount stays as it was, at most the new items
            order.discount = min(order.discount, order.subtotal)
            order.total = order.subtotal - order.discount + e.delivery_charge
            order.updated_at = self.now()
        return order

    async def order(self, number: int) -> ShopOrder | None:
        return await self.by_number(ShopOrder, number)

    async def cancel_order(self, cancel: Confirmed[OrderCancel]) -> None:
        """The buyer cancels before the courier has it; the items go back in stock."""
        found = await self.order(cancel.value.number)
        if found is None:
            raise NotFound("There's no such order.")
        await self.move_order(found.id, "cancelled")

    async def confirm_order(self, confirm: Confirmed[OrderConfirm]) -> ShopOrder:
        """The buyer said yes on the shop's confirmation call: `new` → `confirmed`."""
        found = await self.order(confirm.value.number)
        if found is None:
            raise NotFound("There's no such order.")
        return await self.move_order(found.id, "confirmed")

    async def move_order(self, order_id: str, status: str) -> ShopOrder:
        """The owner moves an order along (or the buyer cancels it). A cancelled order's items
        go back in stock."""
        async with self.sessions() as s, s.begin():
            await self._lock(s)  # cancelled twice at once, its items would go back twice
            order = await self._one(s, ShopOrder, order_id)
            if status != order.status:
                self._move(order, status)
                if status == "cancelled":
                    await self._stock(s, order.lines, +1)
        return order

    async def record_outcome(
        self, order_id: str, paid_in: bool | None, reason: str | None
    ) -> ShopOrder:
        """Once an order ended: whether the rider's cash reached the shop, and why it
        came back or was cancelled. Raises InvalidTransition before then."""
        async with self.sessions() as s, s.begin():
            order = await self._one(s, ShopOrder, order_id)
            if order.status not in ENDED:
                raise InvalidTransition("Money in and a reason are for an order that ended.")
            if paid_in is not None:
                order.paid_in = paid_in
            if reason is not None:
                order.reason = " ".join(reason.split())[:200]
            order.updated_at = self.now()
        return order

    async def _stock(self, s: AsyncSession, lines: list[dict[str, Any]], sign: int) -> None:
        """Takes the lines' quantities out of stock (sign -1) or puts them back (+1): from the
        product's own stock, or from the variant of its size and colour. What isn't
        counted is left alone."""
        rows = {
            row.code: row
            for row in await s.scalars(
                select(ProductRow).where(
                    ProductRow.workspace_id == self.workspace_id,
                    ProductRow.code.in_({line["code"] for line in lines}),
                )
            )
        }
        products = {
            code: Product.model_validate(row, from_attributes=True) for code, row in rows.items()
        }
        wanted: dict[tuple[str, int], int] = {}  # (code, counter) → how many
        for line in lines:
            product = products.get(line["code"])
            counter = product.counter(line["size"], line["colour"]) if product else None
            if counter is not None:
                key = (line["code"], counter)
                wanted[key] = wanted.get(key, 0) + line["quantity"]
        short = []
        for (code, counter), quantity in wanted.items():
            product, row = products[code], rows[code]
            left = product.count(counter)
            if sign < 0 and left < quantity:
                short.append(f"{product.display_name(counter)} ({left} left)")
            if counter == OWN:
                row.stock = left + sign * quantity
            else:  # a new list: a JSON column doesn't see a change in place
                variants = [dict(v) for v in row.variants]
                variants[counter]["stock"] = left + sign * quantity
                row.variants = variants
        if short:
            raise OutOfStock(", ".join(short))
