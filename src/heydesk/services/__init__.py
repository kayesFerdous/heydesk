from dataclasses import dataclass

from heydesk.services.admin import AdminService
from heydesk.services.agents import AgentService
from heydesk.services.channels import ChannelService
from heydesk.services.conversations import ConversationService
from heydesk.services.knowledge import KnowledgeService
from heydesk.services.users import UserService


@dataclass(frozen=True)
class Services:
    admin: AdminService
    agents: AgentService
    channels: ChannelService
    conversations: ConversationService
    knowledge: KnowledgeService
    users: UserService
