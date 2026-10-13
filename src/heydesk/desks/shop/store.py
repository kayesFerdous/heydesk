"""A shop's data: its products and orders, plus what every desk has (`Store`).

Placing an order takes its items out of stock in the same transaction, and cancelling it puts
them back, so two buyers can't get the last one."""

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from heydesk.desks.base import Store
from heydesk.desks.shop.models import ProductRow, ShopOrder
from heydesk.desks.shop.values import CHANGEABLE, NewOrder, OrderCancel, OrderEdit, Product
from heydesk.domain.values import Confirmed
from heydesk.errors import NotFound
from heydesk.models.taken import FIRST_STATUS, UNPAID, next_number


class OutOfStock(ValueError):
    """Someone else bought the last ones since the cart was made."""


class TooLate(ValueError):
    """The order moved on (the courier has it) since the visitor asked to change it."""


class ShopStore(Store):
    async def products(self) -> list[Product]:
        return await self.rows_of(ProductRow, Product)

    async def replace_products(self, products: list[Product]) -> None:
        await self.replace_rows(ProductRow, products)

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

    async def orders_here(self) -> list[ShopOrder]:
        """The orders placed in this conversation, newest first (D55): the visitor needs no
        code for them."""
        if self.conversation_id is None:
            return []
        async with self.sessions() as s:
            found = await s.scalars(
                select(ShopOrder)
                .where(
                    ShopOrder.workspace_id == self.workspace_id,
                    ShopOrder.conversation_id == self.conversation_id,
                )
                .order_by(ShopOrder.created_at.desc())
            )
            return list(found)

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
        """Takes the lines' quantities out of stock (sign -1) or puts them back (+1). Products
        whose stock isn't counted are left alone."""
        wanted: dict[str, int] = {}
        for line in lines:
            wanted[line["code"]] = wanted.get(line["code"], 0) + line["quantity"]
        rows = await s.scalars(
            select(ProductRow).where(
                ProductRow.workspace_id == self.workspace_id, ProductRow.code.in_(wanted)
            )
        )
        short = []
        for row in rows:
            if row.stock is None:
                continue
            if sign < 0 and row.stock < wanted[row.code]:
                short.append(f"{row.name} ({row.stock} left)")
            row.stock += sign * wanted[row.code]
        if short:
            raise OutOfStock(", ".join(short))
