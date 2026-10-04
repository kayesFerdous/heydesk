from dataclasses import dataclass

from heydesk.services.admin import AdminService
from heydesk.services.agents import AgentService
from heydesk.services.conversations import ConversationService
from heydesk.services.items import ItemService
from heydesk.services.orders import OrderService
from heydesk.services.requests import RequestService
from heydesk.services.users import UserService


@dataclass(frozen=True)
class Services:
    admin: AdminService
    agents: AgentService
    conversations: ConversationService
    items: ItemService
    orders: OrderService
    requests: RequestService
    users: UserService
