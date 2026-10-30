"""A restaurant's data: its menu, orders and table bookings, plus what every desk has
(`Store`)."""

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from heydesk.desks.base import Store, TooLate
from heydesk.desks.restaurant.models import Booking, MenuItemRow, Order
from heydesk.desks.restaurant.values import (
    CHANGEABLE,
    HOLDS_SEATS,
    BookingCancel,
    MenuItem,
    NewBooking,
    NewOrder,
    OrderCancel,
    OrderEdit,
    menu_from_csv,
)
from heydesk.domain.values import Confirmed
from heydesk.errors import NotFound
from heydesk.models.customer_request import FIRST_STATUS, UNPAID


class RestaurantStore(Store):
    SALES = (Order, Booking)

    async def menu(self) -> list[MenuItem]:
        return await self.rows_of(MenuItemRow, MenuItem)

    async def replace_menu(self, menu: list[MenuItem]) -> None:
        await self.replace_rows(MenuItemRow, menu)

    async def take_sheet(
        self, text: str, mapping: dict[str, str], seen: dict[str, Any]
    ) -> dict[str, Any]:
        await self.replace_menu(menu_from_csv(text, mapping))
        return {}

    async def place_order(self, order: Confirmed[NewOrder]) -> Order:
        """Saves the order: `new`, or `unpaid` when its delivery charge is paid first."""
        o = order.value
        return await self.add(
            Order,
            status=UNPAID if o.pay_first else FIRST_STATUS,
            name=o.name,
            phone=o.phone,
            lines=o.lines,
            total=o.total,
            currency=o.currency,
            pickup_at=o.pickup_at.astimezone(UTC),
            note=o.note,
            address=o.address,
            delivery_charge=o.delivery_charge,
        )

    async def orders_here(self) -> list[Order]:
        """The orders placed in this conversation, newest first."""
        return await self.records_in_conversation(Order)

    async def order(self, number: int) -> Order | None:
        return await self.by_number(Order, number)

    async def change_order(self, edit: Confirmed[OrderEdit]) -> Order:
        """The new time, address or note. Raises TooLate if the kitchen has it ready by now."""
        e = edit.value
        async with self.sessions() as s, s.begin():
            order = await s.scalar(
                select(Order).where(
                    Order.workspace_id == self.workspace_id, Order.number == e.number
                )
            )
            if order is None:
                raise NotFound("There's no such order.")
            if order.status not in CHANGEABLE:
                raise TooLate(order.status)
            order.pickup_at = e.pickup_at.astimezone(UTC)
            order.address, order.note = e.address, e.note
            order.updated_at = self.now()
        return order

    async def cancel_order(self, cancel: Confirmed[OrderCancel]) -> None:
        found = await self.order(cancel.value.number)
        if found is None:
            raise NotFound("There's no such order.")
        await self.set_status(Order, found.id, "cancelled")

    async def seats_taken(self, around: datetime) -> list[tuple[datetime, int]]:
        """The bookings that hold seats within a day of `around`: when each starts, and its
        people."""
        async with self.sessions() as s:
            rows = await s.execute(
                select(Booking.at, Booking.people).where(
                    Booking.workspace_id == self.workspace_id,
                    Booking.status.in_(HOLDS_SEATS),
                    Booking.at > around - timedelta(days=1),
                    Booking.at < around + timedelta(days=1),
                )
            )
            return [(at, people) for at, people in rows]

    async def book_table(self, booking: Confirmed[NewBooking]) -> Booking:
        b = booking.value
        return await self.add(
            Booking,
            name=b.name,
            phone=b.phone,
            at=b.at.astimezone(UTC),
            people=b.people,
            note=b.note,
        )

    async def booking(self, number: int) -> Booking | None:
        return await self.by_number(Booking, number)

    async def cancel_booking(self, cancel: Confirmed[BookingCancel]) -> None:
        found = await self.booking(cancel.value.number)
        if found is None:
            raise NotFound("There's no such booking.")
        await self.set_status(Booking, found.id, "cancelled")
