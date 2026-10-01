"""Tables. Importing this package registers every one on `Base.metadata` (for Alembic)."""

from heydesk.models.agent import Agent
from heydesk.models.conversation import Conversation
from heydesk.models.workspace import Workspace

__all__ = ["Agent", "Conversation", "Workspace"]
