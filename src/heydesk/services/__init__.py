from dataclasses import dataclass

from heydesk.services.agents import AgentService
from heydesk.services.conversations import ConversationService


@dataclass(frozen=True)
class Services:
    agents: AgentService
    conversations: ConversationService
