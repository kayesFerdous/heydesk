"""What a shop's owner sets: the agent's config, with the shop's delivery, calls and
policies. Pure."""

from datetime import time
from typing import Self

from pydantic import BaseModel, Field, model_validator

from heydesk.runtime.config import AgentConfig, Widget
from heydesk.shop.delivery import Zone


class CallSettings(BaseModel):
    """The shop's calls to confirm new orders: off until the owner turns them on."""

    enabled: bool = False
    wait_minutes: int = Field(default=1, ge=0, le=240)  # after the order, before the call
    start: time = time(10)  # calls only between these, in the business's time
    end: time = time(20)
    tries: int = Field(default=2, ge=1, le=5)  # rings before it gives up
    retry_minutes: int = Field(default=30, ge=1, le=1440)  # between them
    # The call's first line; the buyer says yes to it. Python fills in the tags the owner kept:
    # {business_name}, {items} (what they bought), {total} and {code}. It names the products, as
    # a shop's staff would, not the order code.
    opening: str = Field(
        default=(
            "Assalamu alaikum, this is {business_name}. You ordered {items}, {total} with "
            "delivery, cash on delivery. Shall we send it out?"
        ),
        min_length=1,
        max_length=500,
    )

    @model_validator(mode="after")
    def hours_in_order(self) -> Self:
        if self.end <= self.start:
            raise ValueError("The calling hours must end after they start.")
        return self


class ShopConfig(AgentConfig):
    widget: Widget = Widget(
        starters=["What's new?", "Track my order", "What's the delivery charge?"]
    )
    greeting: str = "Assalamu alaikum! Welcome to {business_name}. How can we help you?"
    # A cash-on-delivery order waits for the shop's call before it's sent (by hand, or
    # `calls`); off: it's confirmed when placed
    call_to_confirm: bool = True
    calls: CallSettings = CallSettings()  # calls to confirm new orders
    delivery: list[Zone] = Field(
        default_factory=list
    )  # where the shop delivers, and the charge for each
    free_delivery_over: int = Field(default=0, ge=0)  # minor units; 0: never free
    policies: str = Field(default="", max_length=4000)  # returns, exchanges
    size_guide: str = Field(default="", max_length=4000)  # each size's measurements, in words
    # A delivered order's size or colour can be swapped within this many days; 0: the
    # agent takes no exchange requests
    exchange_days: int = Field(default=0, ge=0, le=60)

    @property
    def any_zone_pays_first(self) -> bool:
        """Some area's charge is paid online first: `ShopStore.takes_payment_first` reads it."""
        return any(zone.pay_first for zone in self.delivery)

    @model_validator(mode="after")
    def no_calls_without_call_to_confirm(self) -> Self:
        """Off, every order is confirmed when placed or paid, so there is nothing to call
        about: turning it off turns the calls off too, instead of refusing the change."""
        if self.calls.enabled and not self.call_to_confirm:
            self.calls = self.calls.model_copy(update={"enabled": False})
        return self

    @model_validator(mode="after")
    def one_area_for_everywhere_else(self) -> Self:
        if sum(zone.everywhere_else for zone in self.delivery) > 1:
            raise ValueError(
                "Only one delivery area can be for everywhere else: give the others their "
                "districts."
            )
        return self

    def zone(self, name: str) -> Zone | None:
        """The delivery area with this name: an order keeps only its area's name."""
        return next((zone for zone in self.delivery if zone.name == name), None)

    def delivery_charge(self, zone: Zone, subtotal: int) -> int:
        if self.free_delivery_over and subtotal >= self.free_delivery_over:
            return 0
        return zone.charge
