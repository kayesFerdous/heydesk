"""A shop's data: its products and orders, plus what every desk has (`Store`).

Placing an order takes its items out of stock in the same transaction, and cancelling it puts
them back, so two buyers can't get the last one."""

from datetime import datetime
from typing import Any

from sqlalchemy import ScalarResult, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from heydesk.desks.base import Store, TooLate
from heydesk.desks.shop.models import ProductRow, ShopOrder
from heydesk.desks.shop.values import (
    CHANGEABLE,
    HOLDS,
    OWN,
    NewOrder,
    OrderCancel,
    OrderConfirm,
    OrderEdit,
    Product,
    counted,
    merge,
    products_from_csv,
    take_off,
)
from heydesk.domain.values import Confirmed
from heydesk.errors import NotFound
from heydesk.models import Conversation
from heydesk.models.taken import FIRST_STATUS, UNPAID, next_number


class OutOfStock(ValueError):
    """Someone else bought the last ones since the cart was made."""


class ShopStore(Store):
    async def products(self) -> list[Product]:
        return await self.rows_of(ProductRow, Product)

    async def replace_products(self, products: list[Product]) -> None:
        await self.replace_rows(ProductRow, products)

    async def take_sheet(
        self, text: str, mapping: dict[str, str], seen: dict[str, Any]
    ) -> dict[str, Any]:
        """The sheet's products, keeping our count where the sheet's didn't change, and
        taking a new count less the orders not shipped yet (D58). Photos are kept by code."""
        found = products_from_csv(text, mapping)
        ws = self.workspace_id
        async with self.sessions() as s, s.begin():
            rows = await s.scalars(select(ProductRow).where(ProductRow.workspace_id == ws))
            ours = {r.code: Product.model_validate(r, from_attributes=True) for r in rows}
            orders: ScalarResult[list[dict[str, Any]]] = await s.scalars(
                select(ShopOrder.lines).where(
                    ShopOrder.workspace_id == ws, ShopOrder.status.in_(HOLDS)
                )
            )
            held = [line for lines in orders for line in lines]
            await self._replace(s, ProductRow, merge(found, ours, seen, held))
        return {p.code: counted(p) for p in found}

    async def take_woo(self, found: list[Product]) -> None:
        """The WooCommerce store's products become the list (D75), with Woo's count less the
        orders that hold stock here but aren't in Woo yet. Photos are kept by code."""
        ws = self.workspace_id
        async with self.sessions() as s, s.begin():
            photos = {
                code: photos
                for code, photos in await s.execute(
                    select(ProductRow.code, ProductRow.photos).where(ProductRow.workspace_id == ws)
                )
            }
            orders: ScalarResult[list[dict[str, Any]]] = await s.scalars(
                select(ShopOrder.lines).where(
                    ShopOrder.workspace_id == ws,
                    ShopOrder.status.in_(HOLDS),
                    ShopOrder.woo_id.is_(None),
                )
            )
            held = [line for lines in orders for line in lines]
            kept = [
                take_off(p, [line for line in held if line["code"] == p.code]).model_copy(
                    update={"photos": photos.get(p.code, [])}
                )
                for p in found
            ]
            await self._replace(s, ProductRow, kept)

    async def orders_for_woo(self, since: datetime) -> list[ShopOrder]:
        """The orders placed since the store was linked whose status changed since it was
        last written there, oldest first (D75)."""
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
        """The orders the linked courier deals with (D90), oldest first: `confirmed` ones with
        no parcel yet, to book; `shipped` ones with a parcel, to follow."""
        booked = ShopOrder.parcel_id == "" if status == "confirmed" else ShopOrder.parcel_id != ""
        async with self.sessions() as s:
            rows = await s.scalars(
                select(ShopOrder)
                .where(
                    ShopOrder.workspace_id == self.workspace_id,
                    ShopOrder.status == status,
                    booked,
                )
                .order_by(ShopOrder.created_at)
            )
            return list(rows)

    async def orders_to_look_up(self) -> list[ShopOrder]:
        """The orders not sent yet whose mobile's courier record isn't known (D91)."""
        async with self.sessions() as s:
            rows = await s.scalars(
                select(ShopOrder)
                .where(
                    ShopOrder.workspace_id == self.workspace_id,
                    ShopOrder.status.in_(HOLDS),
                    ShopOrder.courier_record == "",
                )
                .order_by(ShopOrder.created_at)
            )
            return list(rows)

    async def record_found(self, order_id: str, record: str) -> None:
        async with self.sessions() as s, s.begin():
            order = await self._one(s, ShopOrder, order_id)
            order.courier_record = record[:200]

    async def parcel_booked(self, order_id: str, parcel_id: str, tracking: str) -> None:
        """The courier has the order's parcel: it's `shipped`, unless it moved meanwhile."""
        async with self.sessions() as s, s.begin():
            order = await self._one(s, ShopOrder, order_id)
            order.parcel_id, order.tracking = parcel_id, tracking
            if order.status == "confirmed":
                self._move(order, "shipped")

    async def courier_said(self, order_id: str, said: str, ending: str | None) -> None:
        """The courier's words about the parcel, and its end: delivered or returned."""
        async with self.sessions() as s, s.begin():
            order = await self._one(s, ShopOrder, order_id)
            order.courier_status = said[:100]
            if ending and order.status == "shipped":
                self._move(order, ending)

    async def place_order(self, order: Confirmed[NewOrder]) -> ShopOrder:
        """Takes the items out of stock and saves the order: `new`, or `unpaid` when its
        delivery charge is paid first (D47). Raises OutOfStock, and saves nothing, if there
        aren't enough left."""
        o = order.value
        async with self.sessions() as s, s.begin():
            await self._stock(s, o.lines, -1)
            now = self.now()
            saved = ShopOrder(
                workspace_id=self.workspace_id,
                conversation_id=self.conversation_id,
                number=await next_number(s, ShopOrder, self.workspace_id),
                created_at=now,
                updated_at=now,
                status=UNPAID if o.pay_first else FIRST_STATUS,
                name=o.name,
                phone=o.phone,
                lines=o.lines,
                subtotal=o.subtotal,
                delivery_charge=o.delivery_charge,
                total=o.total,
                currency=o.currency,
                zone=o.zone,
                address=o.address,
                note=o.note,
            )
            s.add(saved)
        return saved

    async def past_trouble(self, phone: str) -> dict[str, int]:
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

    async def last_order_before(self) -> ShopOrder | None:
        """This buyer's latest order from an earlier conversation, on Telegram or Messenger,
        where the chat says who they are (D81). None on the web and voice: those have no
        contact."""
        if self.conversation_id is None:
            return None
        ws = self.workspace_id
        async with self.sessions() as s:
            here = await s.scalar(
                select(Conversation).where(
                    Conversation.workspace_id == ws, Conversation.id == self.conversation_id
                )
            )
            if here is None or here.contact is None:
                return None
            return await s.scalar(
                select(ShopOrder)
                .join(Conversation, ShopOrder.conversation_id == Conversation.id)
                .where(
                    ShopOrder.workspace_id == ws,
                    Conversation.workspace_id == ws,
                    Conversation.channel == here.channel,
                    Conversation.contact == here.contact,
                    Conversation.id != here.id,
                )
                .order_by(ShopOrder.created_at.desc())
                .limit(1)
            )

    async def orders_here(self) -> list[ShopOrder]:
        """The orders placed in this conversation, newest first (D55)."""
        return await self.here(ShopOrder)

    async def change_order(self, edit: Confirmed[OrderEdit]) -> ShopOrder:
        """New delivery details, and the total again with the new charge. Raises TooLate if
        the courier has it by now."""
        e = edit.value
        async with self.sessions() as s, s.begin():
            order = await s.scalar(
                select(ShopOrder).where(
                    ShopOrder.workspace_id == self.workspace_id, ShopOrder.number == e.number
                )
            )
            if order is None:
                raise NotFound("There's no such order.")
            if order.status not in CHANGEABLE:
                raise TooLate(order.status)
            order.address, order.zone, order.phone, order.note = e.address, e.zone, e.phone, e.note
            order.delivery_charge = e.delivery_charge
            order.total = order.subtotal + e.delivery_charge
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
        """The buyer said yes on the shop's confirmation call (D88): `new` → `confirmed`."""
        found = await self.order(confirm.value.number)
        if found is None:
            raise NotFound("There's no such order.")
        return await self.move_order(found.id, "confirmed")

    async def move_order(
        self, order_id: str, status: str, tracking: str | None = None
    ) -> ShopOrder:
        """The owner moves an order along (or the buyer cancels it). A cancelled order's items
        go back in stock. `tracking` is the courier and its parcel id, for track_order."""
        async with self.sessions() as s, s.begin():
            order = await self._one(s, ShopOrder, order_id)
            if status != order.status:
                self._move(order, status)
                if status == "cancelled":
                    await self._stock(s, order.lines, +1)
            if tracking is not None:
                order.tracking = tracking.strip()
                order.updated_at = self.now()
        return order

    async def _stock(self, s: AsyncSession, lines: list[dict[str, Any]], sign: int) -> None:
        """Takes the lines' quantities out of stock (sign -1) or puts them back (+1): from the
        product's own stock, or from the variant of its size and colour (D56). What isn't
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
                short.append(f"{product.what(counter)} ({left} left)")
            if counter == OWN:
                row.stock = left + sign * quantity
            else:  # a new list: a JSON column doesn't see a change in place
                variants = [dict(v) for v in row.variants]
                variants[counter]["stock"] = left + sign * quantity
                row.variants = variants
        if short:
            raise OutOfStock(", ".join(short))
