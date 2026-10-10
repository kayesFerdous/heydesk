"""A shop's data: its products and orders, plus what every desk has (`Store`).

Placing an order takes its items out of stock in the same transaction, and cancelling it puts
them back, so two buyers can't get the last one."""

from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from heydesk.desks.base import Store
from heydesk.desks.shop.models import ProductRow, ShopOrder
from heydesk.desks.shop.values import NewOrder, OrderCancel, Product
from heydesk.domain.values import Confirmed
from heydesk.errors import HeyDeskError, InvalidTransition, NotFound
from heydesk.models.taken import FIRST_STATUS, UNPAID, next_number


class OutOfStock(ValueError):
    """Someone else bought the last ones since the cart was made."""


class ShopStore(Store):
    async def products(self) -> list[Product]:
        async with self.sessions() as s:
            rows = await s.scalars(
                select(ProductRow)
                .where(ProductRow.workspace_id == self.workspace_id)
                .order_by(ProductRow.position)
            )
            return [Product.model_validate(row, from_attributes=True) for row in rows]

    async def replace_products(self, products: list[Product]) -> None:
        """The shop's products become exactly these, in this order."""
        async with self.sessions() as s, s.begin():
            await s.execute(delete(ProductRow).where(ProductRow.workspace_id == self.workspace_id))
            s.add_all(
                ProductRow(workspace_id=self.workspace_id, position=n, **product.model_dump())
                for n, product in enumerate(products)
            )

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
                try:
                    order.move(status, self.now())
                except LookupError as error:
                    raise HeyDeskError(str(error)) from None
                except ValueError as error:
                    raise InvalidTransition(str(error)) from None
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
