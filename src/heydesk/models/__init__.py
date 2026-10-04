"""Tables. Importing this package registers every one on `Base.metadata` (for Alembic)."""

from heydesk.models.agent import Agent
from heydesk.models.audit import AuditEvent
from heydesk.models.conversation import Conversation
from heydesk.models.item import ItemRow
from heydesk.models.order import Order
from heydesk.models.request import VisitorRequest
from heydesk.models.user import User
from heydesk.models.workspace import Workspace

__all__ = [
    "Agent",
    "AuditEvent",
    "Conversation",
    "ItemRow",
    "Order",
    "User",
    "VisitorRequest",
    "Workspace",
]
