"""The kinds of business HeyDesk serves (design §5). Each is a folder with its own prompt, tools,
tables and routes. To add one: a new folder with a `Desk`, and a line here."""

from heydesk.desks.base import Desk
from heydesk.desks.shared import (
    COMPLAINT,
    HANDOFF,
    OFF_TOPIC,
    REMEMBER,
    SEARCH_KNOWLEDGE,
    TAKE_LEAD,
    TAKE_MESSAGE,
)
from heydesk.desks.shop import SHOP
from heydesk.runtime.config import AgentConfig
from heydesk.runtime.tool import Tool

DESKS: dict[str, Desk] = {desk.kind: desk for desk in (SHOP,)}


def tools_for(desk: Desk, config: AgentConfig, has_documents: bool) -> list[Tool]:
    """A desk's tools for this business, then the shared ones: messages, hand-off,
    a call back, a complaint, off-topic and remember always, and
    the documents search when the business has documents."""
    shared = [TAKE_MESSAGE, TAKE_LEAD, HANDOFF, COMPLAINT, OFF_TOPIC, REMEMBER]
    shared += [SEARCH_KNOWLEDGE] if has_documents else []
    return [*desk.own_tools(config), *shared]
