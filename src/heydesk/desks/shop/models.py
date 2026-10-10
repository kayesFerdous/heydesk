from typing import Any, ClassVar

from sqlalchemy import JSON, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from heydesk.db import Base, new_id
from heydesk.models.taken import Taken


class ProductRow(Base):
    """One of a shop's products (`values.Product`)."""

    __tablename__ = "products"
    __table_args__ = (UniqueConstraint("workspace_id", "code"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    code: Mapped[str] = mapped_column(String(50))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(default="")
    price: Mapped[int]  # minor units
    category: Mapped[str] = mapped_column(String(100))
    sizes: Mapped[list[Any]] = mapped_column(JSON, default=list)
    colours: Mapped[list[Any]] = mapped_column(JSON, default=list)
    stock: Mapped[int | None]  # None: not counted
    position: Mapped[int] = mapped_column(default=0)  # the shop's order, for the prompt


class ShopOrder(Taken, Base):
    """A cash-on-delivery order. Prices are copied in, so a later edit doesn't change it.

    When the shop has the delivery charge paid first, the order starts `unpaid` (its items
    held) and only the payment check makes it `new` (D47); no tool can."""

    __tablename__ = "shop_orders"
    __table_args__ = (UniqueConstraint("workspace_id", "number"),)
    LETTER: ClassVar[str] = "O"
    FLOW: ClassVar[dict[str, set[str]]] = {
        "unpaid": {"new", "cancelled"},  # paid online; or the owner took it another way
        "new": {"confirmed", "cancelled"},  # the shop calls the buyer to confirm
        "confirmed": {"shipped", "cancelled"},
        "shipped": {"delivered", "returned"},  # with the courier
        "delivered": set(),
        "returned": set(),
        "cancelled": set(),
    }

    lines: Mapped[list[Any]] = mapped_column(JSON)  # {code, name, size, colour, quantity, price}
    subtotal: Mapped[int]  # minor units
    delivery_charge: Mapped[int]
    total: Mapped[int]  # the whole order; the rider collects `total - paid`
    paid: Mapped[int] = mapped_column(default=0)  # minor units, paid online first (D47)
    payment_ref: Mapped[str] = mapped_column(String(100), default="")  # the gateway's id for it
    currency: Mapped[str] = mapped_column(String(3))
    zone: Mapped[str] = mapped_column(String(100))
    address: Mapped[str] = mapped_column(String(300))
    note: Mapped[str] = mapped_column(default="")
    tracking: Mapped[str] = mapped_column(String(200), default="")  # the courier and its parcel id
