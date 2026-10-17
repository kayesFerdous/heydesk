from typing import Any, ClassVar

from sqlalchemy import JSON, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from heydesk.db import Base, new_id
from heydesk.desks.shop.values import lines_text
from heydesk.models.taken import Payable, Taken
from heydesk.runtime.speech import money


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
    was_price: Mapped[int | None]  # before a sale; None: not on sale
    category: Mapped[str] = mapped_column(String(100))
    sizes: Mapped[list[Any]] = mapped_column(JSON, default=list)
    colours: Mapped[list[Any]] = mapped_column(JSON, default=list)
    stock: Mapped[int | None]  # None: not counted
    variants: Mapped[list[Any]] = mapped_column(JSON, default=list)  # {size, colour, stock} (D56)
    photos: Mapped[list[Any]] = mapped_column(JSON, default=list)  # photo URLs (D57)
    position: Mapped[int] = mapped_column(default=0)  # the shop's order, for the prompt


class ShopOrder(Taken, Payable, Base):
    """A cash-on-delivery order. Prices are copied in, so a later edit doesn't change it.

    When the shop takes payment first, the order starts `unpaid` (its items held) and only
    the payment check makes it `new` (D47); no tool can."""

    __tablename__ = "shop_orders"
    __table_args__ = (UniqueConstraint("workspace_id", "number"),)
    LETTER: ClassVar[str] = "O"
    PAY_FOR: ClassVar[str] = "the delivery charge"
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
    zone: Mapped[str] = mapped_column(String(100))
    address: Mapped[str] = mapped_column(String(300))
    note: Mapped[str] = mapped_column(default="")
    tracking: Mapped[str] = mapped_column(String(200), default="")  # the courier and its parcel id
    # In a linked WooCommerce store (D75): its id there, and our status as last written ("" never)
    woo_id: Mapped[int | None] = mapped_column(default=None)
    woo_sent: Mapped[str] = mapped_column(String(20), default="")

    @property
    def due(self) -> int:
        return self.delivery_charge

    def about(self) -> str:
        return f"{lines_text(self.lines)}; delivery {self.zone}; to {self.address}"

    def after(self) -> str:
        cod = money(self.total - self.paid, self.currency)
        return (
            f"The rest, {cod}, is paid in cash on delivery. The shop will call you to confirm "
            "before sending it."
        )
