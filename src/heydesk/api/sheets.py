"""The products kept in a Google Sheet: the owner links it once, it's read every few
minutes, and while it's linked the list is changed in the sheet."""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, status
from pydantic import BaseModel, Field

from heydesk.api.deps import Svc, Workspace
from heydesk.models import SheetLink
from heydesk.shop.api import ImportOut
from heydesk.shop.csv_import import Suggestion

router = APIRouter(tags=["sheet"])

URL = Field(
    max_length=500,
    description="The sheet's link, shared as 'Anyone with the link: Viewer'. The tab in it is read",
)


class SheetUrl(BaseModel):
    url: str = URL


class SheetIn(BaseModel):
    url: str = URL
    mapping: dict[str, str] = Field(
        description="Our field → the sheet's column, as the owner checked it after /suggest"
    )
    dry_run: bool = Field(default=False, description="Check the sheet and show what would be saved")


class SheetOut(BaseModel):
    url: str
    synced_at: datetime | None  # the last time it was read cleanly
    problem: str  # empty: fine. Otherwise why the list didn't change, for the owner


def sheet_out(link: SheetLink) -> SheetOut:
    return SheetOut(url=link.url, synced_at=link.synced_at, problem=link.problem)


@router.post("/api/agent/sheet/suggest")
async def suggest_sheet_columns(
    body: SheetUrl, workspace_id: Workspace, services: Svc
) -> Suggestion:
    """Reads the sheet and suggests which of its columns is which of the list's fields."""
    return await services.sheets.suggest(workspace_id, body.url)


@router.put("/api/agent/sheet")
async def link_sheet(body: SheetIn, workspace_id: Workspace, services: Svc) -> ImportOut[Any]:
    """Links the sheet and takes the list from it now, as an import: problems come back per
    row, and nothing is saved until the whole sheet is clean; `dry_run` only checks it. Then
    it's read every few minutes."""
    found = await services.sheets.link(workspace_id, body.url, body.mapping, body.dry_run)
    return ImportOut[Any](saved=not body.dry_run, count=len(found), preview=list(found[:5]))


@router.get("/api/agent/sheet")
async def get_sheet(workspace_id: Workspace, services: Svc) -> SheetOut:
    return sheet_out(await services.sheets.get(workspace_id))


@router.post("/api/agent/sheet/sync")
async def sync_sheet(workspace_id: Workspace, services: Svc) -> SheetOut:
    """Reads the sheet now, instead of waiting for the next few minutes."""
    return sheet_out(await services.sheets.sync(workspace_id))


@router.delete("/api/agent/sheet", status_code=status.HTTP_204_NO_CONTENT)
async def unlink_sheet(workspace_id: Workspace, services: Svc) -> None:
    """Stops reading the sheet. The list stays as it is and can be edited here again."""
    await services.sheets.unlink(workspace_id)
