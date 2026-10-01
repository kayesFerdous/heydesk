"""Items: each agent's dishes, services or products, in the `items` table (D17)."""

from dataclasses import dataclass

from sqlalchemy import ColumnElement, String, cast, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from heydesk.models import ItemRow
from heydesk.runtime.items import Item

SEARCH_LIMIT = 10


def to_item(row: ItemRow) -> Item:
    return Item.model_validate(row, from_attributes=True)


class ItemService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def replace(self, ws: str, agent_id: str, items: list[Item]) -> None:
        """The agent's items become exactly these, in this order."""
        async with self.sessions() as s, s.begin():
            await s.execute(
                delete(ItemRow).where(ItemRow.workspace_id == ws, ItemRow.agent_id == agent_id)
            )
            s.add_all(
                ItemRow(workspace_id=ws, agent_id=agent_id, position=n, **item.model_dump())
                for n, item in enumerate(items)
            )

    async def all(self, ws: str, agent_id: str) -> list[Item]:
        async with self.sessions() as s:
            rows = await s.scalars(
                select(ItemRow)
                .where(ItemRow.workspace_id == ws, ItemRow.agent_id == agent_id)
                .order_by(ItemRow.position)
            )
            return [to_item(row) for row in rows]

    def catalog(self, ws: str, agent_id: str) -> "AgentCatalog":
        return AgentCatalog(self.sessions, ws, agent_id)


@dataclass(frozen=True)
class AgentCatalog:
    """One agent's items, for its tools (`runtime.tool.Catalog`)."""

    sessions: async_sessionmaker[AsyncSession]
    workspace_id: str
    agent_id: str

    def _mine(self) -> list[ColumnElement[bool]]:
        return [ItemRow.workspace_id == self.workspace_id, ItemRow.agent_id == self.agent_id]

    async def get(self, codes: list[str]) -> dict[str, Item]:
        async with self.sessions() as s:
            rows = await s.scalars(select(ItemRow).where(*self._mine(), ItemRow.code.in_(codes)))
            return {row.code: to_item(row) for row in rows}

    async def search(self, query: str) -> list[Item]:
        """Items whose name, category, description or tags contain any of the words.

        Plain word matching, no ranking (design §12). "biryanis" finds "biryani".
        """
        words = [w.removesuffix("s") if len(w) > 3 else w for w in query.lower().split()][:5]
        if not words:
            return []
        text = func.lower(
            ItemRow.name
            + " "
            + ItemRow.category
            + " "
            + ItemRow.description
            + " "
            + cast(ItemRow.tags, String)
        )
        async with self.sessions() as s:
            rows = await s.scalars(
                select(ItemRow)
                .where(*self._mine(), or_(*(text.contains(w, autoescape=True) for w in words)))
                .order_by(ItemRow.position)
                .limit(SEARCH_LIMIT)
            )
            return [to_item(row) for row in rows]
