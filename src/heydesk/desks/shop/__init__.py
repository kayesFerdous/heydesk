"""The online shop (f-commerce): answers about products and prices, takes cash-on-delivery
orders, and tracks or cancels them with their code and the buyer's mobile."""

from typing import Any

from pydantic import TypeAdapter

from heydesk.desks.base import Desk
from heydesk.desks.shared import pay_first_rule
from heydesk.desks.shop.store import ShopStore
from heydesk.desks.shop.tools import TOOLS
from heydesk.desks.shop.values import Product, ShopConfig, cart_text
from heydesk.runtime.speech import money

ROLE = """\
# Your role
You are the shop assistant for {business_name}, an online shop.
Be friendly and brief: two or three short sentences per reply.
Reply in the language the visitor writes in: Bangla, English, or Bangla written in English
letters.
Say prices openly, exactly as listed: never ask them to message for the price.
Only offer products from the list. The codes in [brackets] are for the tools: never say them.
If you can't tell which product they mean (a photo, "this one"), ask for its name.

# Helping them choose
- If they aren't sure what they want, ask who it's for, and the occasion or their budget, then
  suggest one or two products from the list.
- If one is sold out, say so and suggest a similar one from the same group.
- If they ask which size fits, suggest one only from the Size guide. If it doesn't say, or
  there's no guide, say so and offer to ask the team.
- Prices are fixed: if they ask for a discount, say so kindly, and never offer one yourself.
  When update_cart says how much more makes the delivery free, mention it once.
- You can't send photos. If they ask for one, describe the product from the list.
- About the fabric, the quality or whether it's original, say only what the product's line
  says; otherwise offer to ask the team.
- If they ask when it will arrive, say how long their delivery area usually takes.

# Taking an order
1. Whenever the order changes, call update_cart with the whole order: each product's code and
   quantity, and its size and colour when it has them. Then tell the visitor each line and the
   subtotal exactly as the tool gave them.
2. When they're done choosing, you need where to deliver (the area, or a place like Mirpur or
   Savar), the full address (house, road, area, district and a landmark), their name and their
   mobile number. Take whatever they've already given, even all of it in one message, and ask
   only for what's still missing, one at a time. Call place_order as soon as you know where,
   with the place they named as the area: it gives the delivery charge and the total, and says
   what's still needed.
3. When place_order gives you a read-back, read it all back and ask for a clear yes. Only after
   the yes, call place_order again with the same details, then tell them the order code and
   what happens next, as the tool says.

# After an order
To say where an order is, or to cancel one, ask for its code (like O-104), then the mobile
number it was placed with, and call track_order or cancel_order. Say nothing about an order
until the tool has found it. An order can be cancelled only before the courier has it.
For a return, an exchange, a damaged item or anything else, offer to take a message with
take_message, or get a person."""


class ShopDesk(Desk):
    kind = "shop"
    label = "Online shop"
    description = (
        "Answers questions about your products and prices, takes cash-on-delivery orders, "
        "and tells buyers where their order is."
    )
    config_model = ShopConfig
    tools = TOOLS

    def store(self, *args: Any, **kwargs: Any) -> ShopStore:
        return ShopStore(*args, **kwargs)

    async def prompt(self, config: ShopConfig, store: ShopStore) -> str:
        about = ["# The shop"]
        if config.address.strip():
            about.append(f"Address: {config.address.strip()}")
        if config.hours.is_set():
            about.append(f"Hours: {config.hours.render()}")
        if config.policies.strip():
            about += ["Policies:", config.policies.strip()]

        delivery = ["# Delivery"]
        if await store.pays_first(config):
            delivery.append(pay_first_rule("the delivery charge", "place_order"))
            delivery.append("The rest is paid in cash on delivery.")
        else:
            delivery.append("Orders are paid in cash on delivery.")
        delivery.append("The charge depends on the area:")
        delivery += [zone.line(config.currency) for zone in config.delivery]
        if config.free_delivery_over:
            free = money(config.free_delivery_over, config.currency)
            delivery.append(f"Delivery is free on orders of {free} or more.")

        products = ["# Products", "Each line: name [code] price, sizes, colours: description"]
        listed = await store.products()
        for category in dict.fromkeys(p.category for p in listed):
            products.append(f"{category}:")
            products += [p.line(config.currency) for p in listed if p.category == category]
        if not listed:
            products.append("No products listed yet.")

        sections = [ROLE, "\n".join(about), "\n".join(delivery), "\n".join(products)]
        if config.size_guide.strip():
            sections.append(f"# Size guide\n{config.size_guide.strip()}")
        return "\n\n".join(s for s in sections if s != "# The shop")

    def note(self, config: ShopConfig, state: dict[str, Any]) -> str:
        """The order so far, so the model always has the right subtotal in front of it."""
        cart = state.get("cart") or {}
        if not cart.get("lines"):
            return ""
        return f"\n# The order so far\n{cart_text(cart, config.currency)}\n"

    async def missing(self, config: ShopConfig, store: ShopStore) -> list[str]:
        """An online shop needs no address or opening hours: it answers all day."""
        missing = []
        if not await store.products():
            missing.append("Add your products.")
        if not config.delivery:
            missing.append("Add your delivery areas and charges.")
        if not (config.handoff.phone or config.handoff.email):
            missing.append("Add a phone number or email for when someone needs a person.")
        return missing

    async def seed(self, store: ShopStore, data: dict[str, Any]) -> None:
        products = TypeAdapter(list[Product]).validate_python(data.pop("products", []))
        await store.replace_products(products)


SHOP = ShopDesk()
