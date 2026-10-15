from dataclasses import dataclass

from heydesk.services.admin import AdminService
from heydesk.services.agents import AgentService
from heydesk.services.channels import ChannelService
from heydesk.services.conversations import ConversationService
from heydesk.services.imports import ImportService
from heydesk.services.knowledge import KnowledgeService
from heydesk.services.payments import PaymentService
from heydesk.services.photos import PhotoService
from heydesk.services.rate_limits import RateLimiter
from heydesk.services.sheets import SheetService
from heydesk.services.team import TeamService
from heydesk.services.users import UserService
from heydesk.services.visitors import VisitorService


@dataclass(frozen=True)
class Services:
    admin: AdminService
    agents: AgentService
    channels: ChannelService
    conversations: ConversationService
    imports: ImportService
    knowledge: KnowledgeService
    payments: PaymentService
    photos: PhotoService
    rate_limits: RateLimiter
    sheets: SheetService
    team: TeamService  # the owner's alerts, and the business's lines to a visitor
    users: UserService
    visitors: VisitorService
