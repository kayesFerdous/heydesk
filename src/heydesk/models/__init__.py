"""The tables every business has. Each desk keeps its own tables in its folder
(`desks/clinic/models.py`); importing `heydesk.desks` registers those."""

from heydesk.models.agent import Agent
from heydesk.models.audit import AuditEvent
from heydesk.models.channel import Channel
from heydesk.models.conversation import Conversation
from heydesk.models.document import Document
from heydesk.models.payment_account import PaymentAccount
from heydesk.models.sheet_link import SheetLink
from heydesk.models.team_message import TeamMessage
from heydesk.models.turn import Turn
from heydesk.models.user import User
from heydesk.models.visitor import Visitor
from heydesk.models.woo_link import WooLink
from heydesk.models.workspace import Workspace

__all__ = [
    "Agent",
    "AuditEvent",
    "Channel",
    "Conversation",
    "Document",
    "PaymentAccount",
    "SheetLink",
    "TeamMessage",
    "Turn",
    "User",
    "Visitor",
    "WooLink",
    "Workspace",
]
