"""A restaurant's data: its menu and orders, plus what every desk has (`Store`)."""

from datetime import UTC

from sqlalchemy import delete, select

from heydesk.desks.base import Store
from heydesk.desks.restaurant.models import MenuItemRow, Order
from heydesk.desks.restaurant.values import MenuItem, NewOrder
from heydesk.domain.values import Confirmed


class RestaurantStore(Store):
    async def menu(self) -> list[MenuItem]:
        async with self.sessions() as s:
            rows = await s.scalars(
                select(MenuItemRow)
                .where(MenuItemRow.workspace_id == self.workspace_id)
                .order_by(MenuItemRow.position)
            )
            return [MenuItem.model_validate(row, from_attributes=True) for row in rows]

    async def replace_menu(self, menu: list[MenuItem]) -> None:
        """The menu becomes exactly these dishes, in this order."""
        async with self.sessions() as s, s.begin():
            await s.execute(
                delete(MenuItemRow).where(MenuItemRow.workspace_id == self.workspace_id)
            )
            s.add_all(
                MenuItemRow(workspace_id=self.workspace_id, position=n, **item.model_dump())
                for n, item in enumerate(menu)
            )

    async def place_order(self, order: Confirmed[NewOrder]) -> int:
        """Saves the order and returns its number: 101."""
        o = order.value
        saved = await self.add(
            Order,
            name=o.name,
            phone=o.phone,
            lines=o.lines,
            total=o.total,
            currency=o.currency,
            pickup_at=o.pickup_at.astimezone(UTC),
            note=o.note,
        )
        return saved.number
