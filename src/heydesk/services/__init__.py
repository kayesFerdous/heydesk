from dataclasses import dataclass

from heydesk.services.admin import AdminService
from heydesk.services.agents import AgentService
from heydesk.services.conversations import ConversationService
from heydesk.services.knowledge import KnowledgeService
from heydesk.services.users import UserService


@dataclass(frozen=True)
class Services:
    admin: AdminService
    agents: AgentService
    conversations: ConversationService
    knowledge: KnowledgeService
    users: UserService
