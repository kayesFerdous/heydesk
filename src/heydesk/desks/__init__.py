"""The kinds of business HeyDesk serves (design §5). Each is a folder with its own prompt, tools,
tables and routes. To add one: a new folder with a `Desk`, and a line here."""

from heydesk.desks.base import Desk
from heydesk.desks.clinic import CLINIC
from heydesk.desks.receptionist import RECEPTIONIST
from heydesk.desks.restaurant import RESTAURANT
from heydesk.desks.shared import HANDOFF, OFF_TOPIC, SEARCH_KNOWLEDGE, TAKE_MESSAGE
from heydesk.desks.shop import SHOP
from heydesk.runtime.tool import Tool

DESKS: dict[str, Desk] = {desk.kind: desk for desk in (RESTAURANT, CLINIC, RECEPTIONIST, SHOP)}


def tools_for(desk: Desk, has_documents: bool) -> list[Tool]:
    """A desk's tools, then the shared ones: messages, hand-off and off-topic always (D51), and
    the documents search when the business has documents (D35)."""
    shared = [TAKE_MESSAGE, HANDOFF, OFF_TOPIC] + ([SEARCH_KNOWLEDGE] if has_documents else [])
    return [*desk.tools, *shared]
