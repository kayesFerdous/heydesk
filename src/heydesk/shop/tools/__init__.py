"""The shop's tools: what the model can call. Each module is one part of a visit:
products (find, show), cart, orders (place, track, change, cancel, confirm, exchange) and
support (messages, hand-off, complaints, notes, documents)."""

from heydesk.runtime.tool import Tool
from heydesk.shop.tools.cart import UPDATE_CART
from heydesk.shop.tools.orders import (
    CANCEL_ORDER,
    CHANGE_ORDER,
    CONFIRM_ORDER,
    PLACE_ORDER,
    REQUEST_EXCHANGE,
    TRACK_ORDER,
)
from heydesk.shop.tools.products import FIND_PRODUCT, SHOW_PHOTOS
from heydesk.shop.tools.support import (
    FLAG_COMPLAINT,
    HANDOFF_TO_HUMAN,
    OFF_TOPIC,
    REMEMBER,
    SEARCH_KNOWLEDGE,
    TAKE_LEAD,
    TAKE_MESSAGE,
)

SHOP_TOOLS = [
    FIND_PRODUCT,
    UPDATE_CART,
    PLACE_ORDER,
    TRACK_ORDER,
    CHANGE_ORDER,
    CANCEL_ORDER,
    CONFIRM_ORDER,
    REQUEST_EXCHANGE,
    SHOW_PHOTOS,
]


def tools_for(has_documents: bool) -> list[Tool]:
    """The shop's tools, then the support ones: messages, hand-off, a call back, a complaint,
    off-topic and remember always, and the documents search when the business has documents."""
    support = [TAKE_MESSAGE, TAKE_LEAD, HANDOFF_TO_HUMAN, FLAG_COMPLAINT, OFF_TOPIC, REMEMBER]
    support += [SEARCH_KNOWLEDGE] if has_documents else []
    return [*SHOP_TOOLS, *support]
