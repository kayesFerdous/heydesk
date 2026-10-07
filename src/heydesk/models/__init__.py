"""The tables every business has. Each desk keeps its own tables in its folder
(`desks/clinic/models.py`); importing `heydesk.desks` registers those."""

from heydesk.models.agent import Agent
from heydesk.models.audit import AuditEvent
from heydesk.models.channel import Channel
from heydesk.models.conversation import Conversation
from heydesk.models.document import Document
from heydesk.models.team_message import TeamMessage
from heydesk.models.user import User
from heydesk.models.workspace import Workspace

__all__ = [
    "Agent",
    "AuditEvent",
    "Channel",
    "Conversation",
    "Document",
    "TeamMessage",
    "User",
    "Workspace",
]
