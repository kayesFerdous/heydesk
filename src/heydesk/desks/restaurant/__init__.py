"""The restaurant host: answers menu questions, recommends dishes, and takes pickup orders."""

from typing import Any

from pydantic import TypeAdapter

from heydesk.desks.base import Desk, missing_basics
from heydesk.desks.restaurant.store import RestaurantStore
from heydesk.desks.restaurant.tools import TOOLS
from heydesk.desks.restaurant.values import MenuItem, cart_text, menu_text
from heydesk.runtime.config import AgentConfig

ROLE = """\
# Your role
You are the host for {business_name}, a restaurant.
Be warm and brief: two or three short sentences per reply.
When someone wants a recommendation, ask about taste, diet and budget first, one question at a
time, then suggest at most three dishes from the menu, each with a one-line reason.
Only suggest dishes that are on the menu and available.

# Taking a pickup order
Orders are for pickup today only.
1. Whenever the order changes, call update_order with the whole order, then tell the visitor
   the order and its total exactly as the tool gave them.
2. When they're done choosing, ask for the pickup time, then their name, then their phone,
   one at a time. Call place_order as soon as you have the pickup time: it checks we're open
   then and says what's still needed.
3. When place_order gives you a read-back, read it all back and ask for a clear yes. Only after
   the yes, call place_order again with the same details, and tell them the order number.
For anything else, offer to take a message with take_message, or get a person."""


class RestaurantConfig(AgentConfig):
    greeting: str = (
        "Hi, welcome to {business_name}! Hungry? I can help with the menu or take your order."
    )
    policies: str = ""  # allergens disclaimer, payment, parking…


class RestaurantDesk(Desk):
    kind = "restaurant"
    label = "Restaurant host"
    description = "Answers menu questions, recommends dishes, takes pickup orders and messages."
    config_model = RestaurantConfig
    tools = TOOLS

    def store(self, *args: Any, **kwargs: Any) -> RestaurantStore:
        return RestaurantStore(*args, **kwargs)

    async def prompt(self, config: RestaurantConfig, store: RestaurantStore) -> str:
        about = ["# The restaurant"]
        if config.address.strip():
            about.append(f"Address: {config.address.strip()}")
        if config.hours.is_set():
            about.append(f"Opening hours: {config.hours.render()}")
        if config.policies.strip():
            about += ["Policies:", config.policies.strip()]
        menu = "# Menu\n" + menu_text(await store.menu(), config.currency)
        return "\n\n".join([ROLE, "\n".join(about), menu])

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

    async def seed(self, store: RestaurantStore, data: dict[str, Any]) -> None:
        menu = TypeAdapter(list[MenuItem]).validate_python(data.pop("menu", []))
        await store.replace_menu(menu)


RESTAURANT = RestaurantDesk()
