"""Requests (messages for the team): saved only when confirmed (rule 2)."""

from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from heydesk.domain.values import Confirmed, NewRequest
from heydesk.models import VisitorRequest


class RequestService:
    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], now: Callable[[], datetime]
    ) -> None:
        self.sessions = sessions
        self.now = now

    async def submit(
        self, ws: str, agent_id: str, conversation_id: str, request: Confirmed[NewRequest]
    ) -> str:
        """Saves the request and returns its id."""
        row = VisitorRequest(
            workspace_id=ws,
            agent_id=agent_id,
            conversation_id=conversation_id,
            created_at=self.now(),
            **asdict(request.value),
        )
        async with self.sessions() as s, s.begin():
            s.add(row)
        return row.id

    def for_conversation(
        self, ws: str, agent_id: str, conversation_id: str
    ) -> "ConversationRequests":
        return ConversationRequests(self, ws, agent_id, conversation_id)


@dataclass(frozen=True)
class ConversationRequests:
    """One conversation's requests, for `submit_request` (`runtime.tool.Requests`)."""

    service: RequestService
    workspace_id: str
    agent_id: str
    conversation_id: str

    async def submit(self, request: Confirmed[NewRequest]) -> str:
        return await self.service.submit(
            self.workspace_id, self.agent_id, self.conversation_id, request
        )
