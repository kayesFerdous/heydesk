"""Tables. Importing this package registers every one on `Base.metadata` (for Alembic)."""

from heydesk.models.agent import Agent
from heydesk.models.conversation import Conversation
from heydesk.models.item import ItemRow
from heydesk.models.order import Order
from heydesk.models.request import VisitorRequest
from heydesk.models.user import User
from heydesk.models.workspace import Workspace

__all__ = ["Agent", "Conversation", "ItemRow", "Order", "User", "VisitorRequest", "Workspace"]
