from dataclasses import dataclass

from heydesk.services.agents import AgentService
from heydesk.services.conversations import ConversationService
from heydesk.services.items import ItemService


@dataclass(frozen=True)
class Services:
    agents: AgentService
    conversations: ConversationService
    items: ItemService
