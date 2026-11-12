"""A shop's product list kept in a Google Sheet, read every few minutes.

The owner shares the sheet by link and checks which column is which (as an import). Then
the list is the sheet's: each sync reads it as CSV, and when its text changed, the shop's store
takes it (`ShopStore.import_sheet`: the shop keeps its own count where the sheet's didn't
change, and the products it has from elsewhere). A sheet with a problem keeps the old list,
and the owner hears once.
"""

import hashlib
import logging
import re
from collections.abc import Sequence

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from heydesk.errors import Conflict, InvalidConfig, NotFound
from heydesk.models import SheetLink, WooLink
from heydesk.ports.sheets import FetchCsv, SheetUnreadable
from heydesk.services.agents import AgentService
from heydesk.services.team import TeamService
from heydesk.shop.csv_import import PRODUCT_COLUMNS, Suggestion, products_from_csv, suggest

log = logging.getLogger(__name__)

SHEET = re.compile(r"^https://docs\.google\.com/spreadsheets/d/([\w-]+)")
GID = re.compile(r"[#?&]gid=(\d+)")
NOT_A_SHEET = "Paste the link of a Google Sheet: https://docs.google.com/spreadsheets/d/…"


def csv_url(url: str) -> str:
    """The sheet's link, as the owner copies it, → the CSV of that one tab."""
    found = SHEET.match(url.strip())
    if found is None:
        raise InvalidConfig({"url": NOT_A_SHEET})
    tab = GID.search(url)
    gid = f"&gid={tab[1]}" if tab else ""
    return f"https://docs.google.com/spreadsheets/d/{found[1]}/export?format=csv{gid}"


def problems(error: InvalidConfig) -> str:
    """The first few of a sheet's problems, for the owner: "row 3: price: … ; row 7: …"."""
    return "; ".join(f"{where}: {what}" for where, what in list(error.fields.items())[:3])


class SheetService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        agents: AgentService,
        team: TeamService,
        fetch: FetchCsv,
    ) -> None:
        self.sessions = sessions
        self.agents = agents
        self.team = team
        self.fetch = fetch

    async def suggest(self, workspace_id: str, url: str) -> Suggestion:
        """The sheet's columns and their match to a product's fields, for the owner to check."""
        await self.agents.get(workspace_id)  # 404 until it has an agent
        return suggest(await self._read(url), PRODUCT_COLUMNS)

    async def link(
        self, workspace_id: str, url: str, mapping: dict[str, str], dry_run: bool = False
    ) -> Sequence[BaseModel]:
        """Checks the whole sheet with `mapping` (problems per row, as an import) and returns
        its list. Unless `dry_run`, saves the link and takes the list from it, replacing a
        link already there."""
        agent = await self.agents.get(workspace_id)
        async with self.sessions() as s:
            if await s.scalar(select(WooLink.id).where(WooLink.workspace_id == workspace_id)):
                raise Conflict("Unlink the WooCommerce store first: a list comes from one place.")
        text = await self._read(url)
        found = products_from_csv(text, mapping)
        if dry_run:
            return found
        store = await self.agents.store(workspace_id)
        seen = await store.import_sheet(text, mapping, {})  # the first time, every count is new
        async with self.sessions() as s, s.begin():
            old = await s.scalar(select(SheetLink).where(SheetLink.workspace_id == workspace_id))
            if old is not None:
                await s.delete(old)
                await s.flush()
            link = SheetLink(
                workspace_id=workspace_id,
                agent_id=agent.id,
                url=url.strip(),
                mapping=mapping,
                seen=seen,
                checked=hash_text(text),
                synced_at=store.now(),
            )
            s.add(link)
        log.info("sheet linked", extra={"workspace_id": workspace_id})
        return found

    async def get(self, workspace_id: str) -> SheetLink:
        async with self.sessions() as s:
            link = await s.scalar(select(SheetLink).where(SheetLink.workspace_id == workspace_id))
        if link is None:
            raise NotFound("No Google Sheet is linked.")
        return link

    async def unlink(self, workspace_id: str) -> None:
        """Stops syncing. The list stays as it is, and can be edited here again."""
        async with self.sessions() as s, s.begin():
            link = await s.scalar(select(SheetLink).where(SheetLink.workspace_id == workspace_id))
            if link is not None:
                await s.delete(link)

    async def sync(self, workspace_id: str) -> SheetLink:
        """Reads the sheet now. Never raises for the sheet's own problems: they're `problem`."""
        link = await self.get(workspace_id)
        store = await self.agents.store(workspace_id)
        problem, seen, checked = link.problem, link.seen, link.checked
        try:
            text = await self.fetch(csv_url(link.url))
        except SheetUnreadable as error:  # read it all again once it's back
            text, problem, checked = "", str(error), ""
        if text and hash_text(text) != link.checked:  # the same text as last time: nothing to do
            problem, checked = (
                "",
                hash_text(text),
            )  # a problem too isn't read again until it changes
            try:
                seen = await store.import_sheet(text, link.mapping, link.seen)
            except InvalidConfig as error:  # a row the shop can't read: the old list stays
                problem = problems(error)
        async with self.sessions() as s, s.begin():
            saved = await s.get_one(SheetLink, link.id)
            told = saved.problem
            saved.problem, saved.seen, saved.checked = problem, seen, checked
            if not problem:
                saved.synced_at = store.now()
        if problem and problem != told:
            await self.team.notify(workspace_id, f"⚠️ Your Google Sheet wasn't read: {problem}")
        log.info("sheet synced", extra={"workspace_id": workspace_id, "problem": bool(problem)})
        return saved

    async def sync_all(self) -> None:
        """Syncs every linked sheet, one after another (a background job). One sheet failing
        doesn't stop the others."""
        async with self.sessions() as s:
            linked = list(await s.scalars(select(SheetLink.workspace_id)))
        for workspace_id in linked:
            try:
                await self.sync(workspace_id)
            except Exception:
                log.exception("sheet sync failed", extra={"workspace_id": workspace_id})

    async def _read(self, url: str) -> str:
        try:
            return await self.fetch(csv_url(url))
        except SheetUnreadable as error:
            raise InvalidConfig({"url": str(error)}) from None


def hash_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()
