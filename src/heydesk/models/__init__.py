"""Tables. Importing this package registers every one on `Base.metadata` (for Alembic)."""

from heydesk.models.agent import Agent
from heydesk.models.audit import AuditEvent
from heydesk.models.conversation import Conversation
from heydesk.models.document import Document
from heydesk.models.item import ItemRow
from heydesk.models.record import Record
from heydesk.models.user import User
from heydesk.models.workspace import Workspace

__all__ = [
    "Agent",
    "AuditEvent",
    "Conversation",
    "Document",
    "ItemRow",
    "Record",
    "User",
    "Workspace",
]
