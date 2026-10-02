from dataclasses import dataclass

from heydesk.services.agents import AgentService
from heydesk.services.conversations import ConversationService
from heydesk.services.items import ItemService
from heydesk.services.orders import OrderService


@dataclass(frozen=True)
class Services:
    agents: AgentService
    conversations: ConversationService
    items: ItemService
    orders: OrderService
