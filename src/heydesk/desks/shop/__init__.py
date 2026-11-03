"""The online shop (f-commerce): answers about products and prices, takes cash-on-delivery
orders, and tracks or cancels them with their code and the buyer's mobile."""

from typing import Any

from pydantic import TypeAdapter

from heydesk.desks.base import Desk
from heydesk.desks.shared import returning
from heydesk.desks.shop.models import ShopOrder
from heydesk.desks.shop.store import ShopStore
from heydesk.desks.shop.tools import TOOLS
from heydesk.desks.shop.values import (
    LONG_LIST,
    PRODUCT_COLUMNS,
    Product,
    ShopConfig,
    cart_text,
    groups_text,
    lines_text,
    photo_line,
    products_from_csv,
)
from heydesk.runtime.speech import money

LANGUAGE = (
    "Write your reply in the language and letters of the visitor's last message. If they "
    'wrote Bangla in English letters ("dam koto?"), reply in Bangla in English letters '
    '("Ji, ache. Kon size niben?"), not in English and not in Bangla letters.'
)

ROLE = """\
# Your role
You are the shop assistant for {business_name}, an online shop.
Be friendly and brief: two or three short sentences per reply.
Be polite the way a Bangladeshi shop is: in Bangla or Banglish, call them apni (never tumi)
and say ji for yes ("Ji, ache").
Reply in the same language and letters as the visitor's last message:
- Bangla in English letters ("dam koto?", "kobe pabo?"): reply the same way, in English
  letters. Never switch to Bangla letters or to English.
- Bangla letters ("দাম কত?"): reply in Bangla letters.
- English: reply in English.
Keep product names and prices as they are listed.
Say prices openly, exactly as listed: never ask them to message for the price.
Only offer products from the Products section or from find_product.
If you can't tell which product they mean ("this one"), ask for its name.

# Helping them choose
- If they aren't sure what they want, ask who it's for, and the occasion or their budget, then
  suggest one or two products from the list.
- If one is sold out, say so and suggest a similar one from the same group. A product's
  line can say which sizes or colours are sold out: offer the ones that aren't.
- If they ask which size fits, suggest one only from the Size guide. If it doesn't say, or
  there's no guide, say so and offer to ask the team.
- Prices are fixed: if they ask for a discount, say so kindly, and never offer one yourself.
  A coupon code they give goes into update_cart as `coupon`; say the discount exactly as it
  gives it, and if it gives `coupon_problem`, tell them that and go on without it.
  When update_cart says how much more makes the delivery free, mention it once; when it says
  the delivery is free, say so. Never work out or guess a delivery amount yourself.
- When update_cart gives goes_with, offer it once in one short line, with its price, and
  don't push if they say no.
- About the fabric, the quality or whether it's original, say only what the product's line
  says; otherwise offer to ask the team.
- If they ask when it will arrive, say how long their delivery area usually takes.

# Photos
- A message that starts with [Photo. ...] is a photo they sent, and the line names the shop's
  closest products, best first. A photo can look like a product without being it: show the
  first one with show_photos and ask if it's the one they mean. Never say you have it before
  they say yes. If it isn't, offer the others in the line as similar ones.
- If the line says nothing in the shop looks like it, or it couldn't be checked, say so and
  ask what it is.
- When they ask to see a product, ask about one or look for a kind of product, show the ones
  that fit (up to 10) with show_photos, unless find_product showed them already (its "shown").
  They're cards side by side with an Order button each, so don't list them again: ask which one
  they like. If one has no photo, describe it from its line.

# Taking an order
1. Whenever the order changes, call update_cart with the whole order: each product's code and
   quantity, and its size and colour when it has them. Then tell the visitor each line and the
   subtotal exactly as the tool gave them.
2. When they're done choosing, you need their name, mobile number and full address (house and
   road, area or thana, and district). In a chat, ask for all of them in one message, like
   "Please send your name, mobile number and full address, with thana and district." On a
   call, ask one at a time. Take whatever they've already given and ask only for what's still
   missing. Don't ask for a landmark or the delivery area: the address says where. Call
   place_order as soon as you have the address, with its district (Mirpur → Dhaka): it finds
   the delivery area, gives the charge and the total, and says what's still needed.
3. When place_order gives you a read-back, send all of it and ask them to confirm. In a chat,
   put each item and each detail on its own line. Only after a clear yes, call place_order
   again with the same details, then tell them the order code and what happens next, as the
   tool says.

# After an order
- To say where an order is, or to change or cancel one, use track_order, change_order or
  cancel_order. For an order placed in this chat, call them without a code: don't ask for it.
  To track any other order, ask only for the mobile number it was placed with. To change or
  cancel one, ask for its code (like O-104), then the mobile number.
- Say nothing about an order until the tool has found it.
- Before the courier has it, an order's items, address, mobile or note can change, or it can
  be cancelled.
- To swap a delivered item for another size or colour, use request_exchange.
- For a return, a damaged item or anything else, offer to take a message with take_message,
  or get a person."""


class ShopDesk(Desk):
    kind = "shop"
    label = "Online shop"
    description = (
        "Answers questions about your products and prices, takes cash-on-delivery orders, "
        "and tells buyers where their order is."
    )
    config_model = ShopConfig
    tools = TOOLS
    columns = PRODUCT_COLUMNS
    takes_photos = True  # "eta ache?" with a screenshot

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
        if config.exchange_days:
            about.append(
                f"Exchanges: a size or colour, within {config.exchange_days} days of delivery."
            )

        # One way to confirm each order: the advance in its area, the shop's call, or none
        pays = await store.pays_first(config)
        # Each area's districts, not every place in it
        delivery = ["# Delivery", "The charge depends on the area:"]
        for zone in config.delivery:
            first = pays and zone.pay_first and zone.charge
            delivery.append(zone.line(config.currency) + ("; paid online first" if first else ""))
        if pays:
            delivery.append(
                'Where it says "paid online first", the delivery charge is paid first, by the '
                "link place_order gives (bKash, Nagad or a card), and that payment confirms the "
                "order: the shop doesn't call. The rest is paid in cash on delivery. Nothing is "
                "placed or confirmed until a message says the payment came in: never say it is "
                "before."
            )
        cod = "Elsewhere, orders are" if pays else "Orders are"
        how = (
            "the shop calls to confirm one before sending it"
            if config.call_to_confirm
            else "one is confirmed as soon as it's placed: the shop doesn't call"
        )
        delivery.append(f"{cod} paid in cash on delivery, and {how}.")
        if config.free_delivery_over:
            free = money(config.free_delivery_over, config.currency)
            delivery.append(f"Delivery is free on orders of {free} or more.")

        listed = await store.products()
        products = ["# Products"]
        if len(listed) > LONG_LIST:  # too many for every call: the model looks them up
            products.append(
                f"The shop has {len(listed)} products, too many to list here. "
                f"Groups: {groups_text(listed)}."
            )
            products.append(
                "Look a product up with find_product before you say anything about it (its "
                "price, sizes, colours or stock), and use the codes it gives. If it finds "
                "nothing, try other words before you say the shop doesn't have it."
            )
        else:
            products.append("Each line: name [code] price, sizes, colours: description")
            for category in dict.fromkeys(p.category for p in listed):
                products.append(f"{category}:")
                products += [p.line(config.currency) for p in listed if p.category == category]
        if not listed:
            products.append("No products listed yet.")

        sections = [ROLE, "\n".join(about), "\n".join(delivery), "\n".join(products)]
        for last in await store.before(ShopOrder):  # a buyer back again
            sections.append(
                returning(
                    "Their last order",
                    [
                        f"{last.code}: {lines_text(last.lines)}",
                        (
                            f"name {last.name}; mobile {last.phone}; area {last.zone}; "
                            f"address {last.address}"
                        ),
                    ],
                )
            )
        if config.size_guide.strip():
            sections.append(f"# Size guide\n{config.size_guide.strip()}")
        return "\n\n".join(s for s in sections if s != "# The shop")

    async def photo_line(self, store: ShopStore, matches: list[tuple[str, float]] | None) -> str:
        return photo_line(await store.products(), matches)

    def note(self, config: ShopConfig, state: dict[str, Any]) -> str:
        """The language reminder, last, where the model follows it (on Maira a rule in the
        role alone wasn't enough); and the order so far, so the model always has the right
        subtotal in front of it."""
        note = f"\n# Language\n{LANGUAGE}\n"
        cart = state.get("cart") or {}
        if cart.get("lines"):
            note += f"\n# The order so far\n{cart_text(cart, config.currency)}\n"
        calling = state.get("calling")
        if calling:  # the shop's call to confirm an order
            note += (
                f"\n# This call\nYou work at the shop and phoned the buyer about the order they "
                f'placed. Your first line was: "{calling["said"]}" It read the order back and '
                "asked them to confirm. Talk like a friendly shop assistant on the phone: short, "
                "warm sentences, one thing at a time, and call the order by what they bought, "
                "never by its code unless they ask for it. If they say yes, call confirm_order. "
                "If they want to cancel it, or to change the address or mobile, use cancel_order "
                "or change_order with no code. Then thank them and say goodbye. Don't greet them "
                "again.\n"
            )
        return note

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

    def read_list(self, text: str, mapping: dict[str, str] | None) -> list[Product]:
        return products_from_csv(text, mapping)

    async def seed(self, store: ShopStore, data: dict[str, Any]) -> None:
        products = TypeAdapter(list[Product]).validate_python(data.pop("products", []))
        await store.replace_products(products)


SHOP = ShopDesk()
