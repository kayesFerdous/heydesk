"""The restaurant host: answers menu questions, recommends dishes, and takes pickup orders,
delivery orders when the restaurant delivers, and table bookings when it takes them."""

from typing import Any

from pydantic import TypeAdapter

from heydesk.desks.base import Desk, missing_basics
from heydesk.desks.restaurant.store import RestaurantStore
from heydesk.desks.restaurant.tools import BOOKING_TOOLS, TOOLS
from heydesk.desks.restaurant.values import (
    MENU_COLUMNS,
    MenuItem,
    RestaurantConfig,
    cart_text,
    menu_from_csv,
    menu_text,
)
from heydesk.desks.shared import pay_first_rule
from heydesk.runtime.parse import DAYS_AHEAD
from heydesk.runtime.speech import money
from heydesk.runtime.tool import Tool

ROLE = """\
# Your role
You are the host for {business_name}, a restaurant.
Be warm and brief: two or three short sentences per reply.
When someone wants a recommendation, ask about taste, diet and budget first, one question at a
time, then suggest at most three dishes from the menu, each with a one-line reason.
Only suggest dishes that are on the menu and available."""

TAKING_AN_ORDER = """# Taking {kind} order
{when}
1. Whenever the order changes, call update_order with the whole order, then tell the visitor
   the order and its total exactly as the tool gave them.
2. When they're done choosing, {ask}, one at a time. Call place_order as soon as you have the
   time: it checks we're open then and says what's still needed.
3. When place_order gives you a read-back, read it all back and ask for a clear yes. Only after
   the yes, call place_order again with the same details, and tell them the order code.
For anything else, offer to take a message with take_message, or get a person.

# After an order
- To say where an order is, or to change or cancel one, use track_order, change_order or
  cancel_order. For an order placed in this chat, call them without a code: don't ask for it.
  For any other order, ask for its code (like O-104), then the phone it was placed with.
- Say nothing about an order until the tool has found it.
- Until it's ready, an order's time, address or note can change, or it can be cancelled. To
  change the dishes, they cancel it and order again."""

PICKUP = {
    "kind": "a pickup",
    "when": "Orders are for pickup today only.",
    "ask": "ask for the pickup time, then their name, then their phone",
}
DELIVERY = {
    "kind": "an",
    "when": "Orders are for today, picked up or delivered.",
    "ask": "ask whether it's pickup or delivery, then the time, then for a delivery the full "
    "address with a landmark, then their name, then their phone",
}

BOOKINGS = """# Table bookings
We take tables for up to {max_party} people, up to {days} days ahead.
1. Ask how many people, then the day and time, one at a time. Call book_table as soon as you
   have both: it checks we have room then, and gives other times when we're full.
2. Then ask their name, then their phone, then whether there's anything we should know.
3. When book_table gives you a read-back, read it all back and ask for a clear yes. Only after
   the yes, call book_table again with the same details, and tell them the booking code.
To cancel a booking, ask for its code (like T-101), then the name it's under, and use
cancel_booking."""


class RestaurantDesk(Desk):
    kind = "restaurant"
    label = "Restaurant host"
    description = (
        "Answers menu questions, recommends dishes, takes pickup or delivery orders, table "
        "bookings and messages."
    )
    config_model = RestaurantConfig
    tools = TOOLS
    columns = MENU_COLUMNS

    def store(self, *args: Any, **kwargs: Any) -> RestaurantStore:
        return RestaurantStore(*args, **kwargs)

    def own_tools(self, config: RestaurantConfig) -> list[Tool]:
        return TOOLS + (BOOKING_TOOLS if config.takes_bookings else [])

    async def prompt(self, config: RestaurantConfig, store: RestaurantStore) -> str:
        about = ["# The restaurant"]
        if config.address.strip():
            about.append(f"Address: {config.address.strip()}")
        if config.hours.is_set():
            about.append(f"Opening hours: {config.hours.render()}")
        if config.policies.strip():
            about += ["Policies:", config.policies.strip()]
        words = DELIVERY if config.delivers else PICKUP
        orders = TAKING_AN_ORDER
        for key, value in words.items():
            orders = orders.replace("{" + key + "}", value)
        if config.delivers:
            charge = money(config.delivery_charge, config.currency)
            about.append(
                f"Delivery: {charge} to any address."
                if config.delivery_charge
                else "Delivery is free."
            )
        if await store.pays_first(config):
            about.append(pay_first_rule("the delivery charge", "place_order"))
            about.append("The rest is paid on delivery. Pickup orders pay nothing first.")
        sections = [ROLE, orders]
        if config.takes_bookings:
            sections.append(
                BOOKINGS.replace("{max_party}", str(config.max_party)).replace(
                    "{days}", str(DAYS_AHEAD)
                )
            )
        menu = "# Menu\n" + menu_text(await store.menu(), config.currency)
        return "\n\n".join([*sections, "\n".join(about), menu])

    def note(self, config: RestaurantConfig, state: dict[str, Any]) -> str:
        """The order so far, so the model always has the right total in front of it."""
        cart = state.get("cart") or {}
        if not cart.get("lines"):
            return ""
        return f"\n# The order so far\n{cart_text(cart, config.currency)}\n"

    async def missing(self, config: RestaurantConfig, store: RestaurantStore) -> list[str]:
        missing = missing_basics(config)
        if not await store.menu():
            missing.append("Add your menu.")
        return missing

    def read_list(self, text: str, mapping: dict[str, str] | None) -> list[MenuItem]:
        return menu_from_csv(text, mapping)

    async def seed(self, store: RestaurantStore, data: dict[str, Any]) -> None:
        menu = TypeAdapter(list[MenuItem]).validate_python(data.pop("menu", []))
        await store.replace_menu(menu)


RESTAURANT = RestaurantDesk()
