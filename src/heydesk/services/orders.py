"""Orders: saved only when confirmed (rule 2)."""

from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from heydesk.domain.values import Confirmed, NewOrder
from heydesk.errors import NotFound
from heydesk.models import Order

FIRST_NUMBER = 101  # an agent's first order; "order 1" sounds like a test


class OrderService:
    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], now: Callable[[], datetime]
    ) -> None:
        self.sessions = sessions
        self.now = now

    async def place(
        self, ws: str, agent_id: str, conversation_id: str, order: Confirmed[NewOrder]
    ) -> int:
        """Saves the order with the agent's next number, and returns the number."""
        async with self.sessions() as s, s.begin():
            last = await s.scalar(
                select(func.max(Order.number)).where(
                    Order.workspace_id == ws, Order.agent_id == agent_id
                )
            )
            number = last + 1 if last else FIRST_NUMBER
            s.add(
                Order(
                    workspace_id=ws,
                    agent_id=agent_id,
                    conversation_id=conversation_id,
                    number=number,
                    created_at=self.now(),
                    **asdict(order.value),
                )
            )
        return number

    async def list(self, ws: str, conversation_id: str | None = None) -> list[Order]:
        """Newest first."""
        query = select(Order).where(Order.workspace_id == ws).order_by(Order.created_at.desc())
        if conversation_id:
            query = query.where(Order.conversation_id == conversation_id)
        async with self.sessions() as s:
            return list(await s.scalars(query))

    async def set_status(self, ws: str, order_id: str, status: str) -> Order:
        async with self.sessions() as s, s.begin():
            order = await s.scalar(
                select(Order).where(Order.id == order_id, Order.workspace_id == ws)
            )
            if order is None:
                raise NotFound("There's no such order.")
            order.status = status
        return order

    def for_conversation(
        self, ws: str, agent_id: str, conversation_id: str
    ) -> "ConversationOrders":
        return ConversationOrders(self, ws, agent_id, conversation_id)


@dataclass(frozen=True)
class ConversationOrders:
    """One conversation's orders, for `place_order` (`runtime.tool.Orders`)."""

    service: OrderService
    workspace_id: str
    agent_id: str
    conversation_id: str

    async def place(self, order: Confirmed[NewOrder]) -> int:
        return await self.service.place(
            self.workspace_id, self.agent_id, self.conversation_id, order
        )
