from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import JSON, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from heydesk.db import Base, new_id
from heydesk.models.customer_request import CustomerRequest
from heydesk.runtime.speech import money
from heydesk.shop.values import lines_text


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
    variants: Mapped[list[Any]] = mapped_column(JSON, default=list)  # {size, colour, stock}
    photos: Mapped[list[Any]] = mapped_column(JSON, default=list)  # photo URLs
    videos: Mapped[list[Any]] = mapped_column(JSON, default=list)  # video links
    goes_with: Mapped[list[Any]] = mapped_column(JSON, default=list)  # product codes
    position: Mapped[int] = mapped_column(default=0)  # the shop's order, for the prompt


class CouponRow(Base):
    """A shop's coupon code (`values.Coupon`)."""

    __tablename__ = "coupons"
    __table_args__ = (UniqueConstraint("workspace_id", "code"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    code: Mapped[str] = mapped_column(String(30))
    kind: Mapped[str] = mapped_column(String(20))  # percent, amount or free_delivery
    value: Mapped[int] = mapped_column(default=0)
    ends_at: Mapped[datetime | None] = mapped_column(default=None)
    max_uses: Mapped[int | None] = mapped_column(default=None)
    used: Mapped[int] = mapped_column(default=0)
    # An offer: only these chats, the ones it was sent to, may use it. None: anyone
    only_for: Mapped[list[str] | None] = mapped_column(JSON(none_as_null=True), default=None)


class ShopOrder(CustomerRequest, Base):
    """A cash-on-delivery order. Prices are copied in, so a later edit doesn't change it.

    When the shop takes payment first, the order starts `unpaid` (its items held) and only
    the payment check moves it on; no tool can. The advance is the confirmation, so a
    paid order is `confirmed`, with no call."""

    __tablename__ = "shop_orders"
    __table_args__ = (UniqueConstraint("workspace_id", "number"),)
    CODE_LETTER: ClassVar[str] = "O"
    PAY_FOR: ClassVar[str] = "the delivery charge"  # what's paid online first
    STATUS_FLOW: ClassVar[dict[str, set[str]]] = {
        "unpaid": {"confirmed", "cancelled"},  # paid online; or the owner took it another way
        "new": {"confirmed", "on_hold", "cancelled"},  # the shop calls the buyer to confirm
        "on_hold": {"confirmed", "cancelled"},  # waiting on the buyer or the shop
        "confirmed": {"packed", "on_hold", "shipped", "cancelled"},
        "packed": {"shipped", "on_hold", "cancelled"},  # ready for the courier
        "shipped": {"delivered", "returned"},  # with the courier
        "delivered": set(),
        "returned": set(),
        "cancelled": set(),
    }

    currency: Mapped[str] = mapped_column(String(3))
    paid: Mapped[int] = mapped_column(default=0)  # minor units, paid online first
    payment_ref: Mapped[str] = mapped_column(String(100), default="")  # the gateway's id for it
    lines: Mapped[list[Any]] = mapped_column(JSON)  # {code, name, size, colour, quantity, price}
    subtotal: Mapped[int]  # minor units
    delivery_charge: Mapped[int]
    discount: Mapped[int] = mapped_column(default=0)  # a coupon took this off the items
    coupon: Mapped[str] = mapped_column(String(30), default="")
    total: Mapped[int]  # subtotal - discount + delivery_charge; the rider collects `total - paid`
    zone: Mapped[str] = mapped_column(String(100))
    address: Mapped[str] = mapped_column(String(300))
    note: Mapped[str] = mapped_column(default="")
    # Where it was placed: "chat", "page" (*Buy now*) or "team"
    source: Mapped[str] = mapped_column(String(10), default="chat", server_default="chat")
    # In a linked WooCommerce store: its id there, and our status as last written ("" never)
    woo_id: Mapped[int | None] = mapped_column(default=None)
    woo_sent: Mapped[str] = mapped_column(String(20), default="")
    # Booked with the courier: its parcel id ("" not booked), and its last words
    parcel_id: Mapped[str] = mapped_column(String(50), default="")
    courier_status: Mapped[str] = mapped_column(String(100), default="")
    # Once it ends: the rider's cash reached the shop, and why it came back or was
    # cancelled ("refused", "damaged")
    paid_in: Mapped[bool] = mapped_column(default=False)
    reason: Mapped[str] = mapped_column(String(200), default="")
    # Told once each, sent or not (outside the app's window it isn't): out for
    # delivery, and a day after it was delivered, how was it
    reminded: Mapped[bool] = mapped_column(default=False)
    review_asked: Mapped[bool] = mapped_column(default=False)

    @property
    def amount_due(self) -> int:
        """What's paid online first, in minor units."""
        return self.delivery_charge

    def alert_summary(self) -> str:
        """A line for the owner's alert once it's paid: what was ordered."""
        return f"{lines_text(self.lines)}; delivery {self.zone}; to {self.address}"

    def status_update_text(self) -> str:
        """What the buyer is told in their chat when the owner or the courier moves it here; "" for nothing."""
        if self.status == "confirmed":
            return f"✅ Your order {self.code} is confirmed and will be sent out."
        if self.status == "shipped":
            return (
                f"📦 Your order {self.code} is with the courier. The delivery man calls before "
                "he comes."
            )
        if self.status == "delivered":
            return f"🎉 Your order {self.code} was delivered. Thank you for shopping with us!"
        if self.status == "cancelled":
            return f"Sorry, the shop had to cancel your order {self.code}."
        return ""

    def next_step_text(self) -> str:
        """What the visitor is told happens next, once it's paid."""
        cash_due = money(self.total - self.paid, self.currency)
        return (
            f"Your order {self.code} is confirmed and will be sent out. Pay the rest, {cash_due}, to "
            "the delivery man, who calls before he comes."
        )
