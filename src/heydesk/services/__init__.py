from dataclasses import dataclass

from heydesk.ports.transcriber import Transcriber
from heydesk.services.admin import AdminService
from heydesk.services.agents import AgentService
from heydesk.services.assist import AssistService
from heydesk.services.billing import BillingService
from heydesk.services.calls import ConfirmationCallService
from heydesk.services.channel_links import ChannelLinkService
from heydesk.services.channels import ChannelService
from heydesk.services.conversations import ConversationService
from heydesk.services.courier import CourierService
from heydesk.services.inbox import InboxService
from heydesk.services.knowledge import KnowledgeService
from heydesk.services.live import LiveHub
from heydesk.services.members import MemberService
from heydesk.services.orders import OrderService
from heydesk.services.payments import PaymentService
from heydesk.services.photos import PhotoService
from heydesk.services.rate_limits import RateLimiter
from heydesk.services.sheets import SheetService
from heydesk.services.team import TeamService
from heydesk.services.users import UserService
from heydesk.services.visitors import VisitorService
from heydesk.services.woo import WooService


@dataclass(frozen=True)
class Services:
    admin: AdminService
    agents: AgentService
    assist: AssistService  # the model's help for the team on a chat
    billing: BillingService  # paying HeyDesk for a plan
    calls: ConfirmationCallService  # the shop's call to confirm an order, to a /phone page
    channel_links: ChannelLinkService  # connecting Telegram, Messenger and WhatsApp
    channels: ChannelService  # answering them
    conversations: ConversationService
    courier: CourierService  # books and follows a shop's parcels
    inbox: InboxService  # the team's side of the chats
    knowledge: KnowledgeService
    live: LiveHub  # tells an open inbox a chat changed
    members: MemberService  # the business's moderators and what its team did
    orders: OrderService  # what follows a shop order: its alert, its way into WooCommerce
    payments: PaymentService
    photos: PhotoService
    rate_limits: RateLimiter
    sheets: SheetService
    team: TeamService  # the owner's alerts, and the business's lines to a visitor
    transcriber: Transcriber | None  # speech in words: voice notes, and calls on Chorus
    users: UserService
    visitors: VisitorService
    woo: WooService  # a shop's linked WooCommerce store
