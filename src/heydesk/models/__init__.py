"""The tables every business has. Each desk keeps its own tables in its folder
(`desks/shop/models.py`); importing `heydesk.desks` registers those."""

from heydesk.models.agent import Agent
from heydesk.models.audit import AuditEvent
from heydesk.models.call import Call
from heydesk.models.channel import Channel
from heydesk.models.conversation import Conversation
from heydesk.models.courier_link import CourierLink
from heydesk.models.document import Document
from heydesk.models.lead import Lead
from heydesk.models.missed_question import MissedQuestion
from heydesk.models.note import Note
from heydesk.models.page_post import PagePost
from heydesk.models.payment_account import PaymentAccount
from heydesk.models.post_comment import PostComment
from heydesk.models.sheet_link import SheetLink
from heydesk.models.subscription_payment import SubscriptionPayment
from heydesk.models.team_event import TeamEvent
from heydesk.models.team_message import TeamMessage
from heydesk.models.ticket import Ticket
from heydesk.models.turn import Turn
from heydesk.models.user import User
from heydesk.models.visitor import Visitor
from heydesk.models.woo_link import WooLink
from heydesk.models.workspace import Workspace

__all__ = [
    "Agent",
    "AuditEvent",
    "Call",
    "Channel",
    "Conversation",
    "CourierLink",
    "Document",
    "Lead",
    "MissedQuestion",
    "Note",
    "PagePost",
    "PaymentAccount",
    "PostComment",
    "SheetLink",
    "SubscriptionPayment",
    "TeamEvent",
    "TeamMessage",
    "Ticket",
    "Turn",
    "User",
    "Visitor",
    "WooLink",
    "Workspace",
]
