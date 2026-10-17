"""A shop's list kept in its WooCommerce store, and our orders written there (D75): the owner
links it once with a Read/Write key, and it's synced every few minutes."""

from datetime import datetime

from fastapi import APIRouter, status
from pydantic import BaseModel, Field

from heydesk.api.deps import Svc, Workspace
from heydesk.models import WooLink

router = APIRouter(tags=["woo"])


class WooIn(BaseModel):
    url: str = Field(max_length=500, description="The store's address: https://shop.example.com")
    key: str = Field(max_length=100, description="A REST API key made Read/Write in WooCommerce")
    secret: str = Field(max_length=100, description="The key's secret")


class WooOut(BaseModel):
    url: str
    products: int  # how many the store has that can be bought
    synced_at: datetime | None  # the last clean sync
    problem: str  # empty: fine. Otherwise what went wrong, for the owner


def woo_out(link: WooLink) -> WooOut:
    return WooOut(
        url=link.url, products=len(link.ids), synced_at=link.synced_at, problem=link.problem
    )


@router.put("/api/agent/woo")
async def link_woo(body: WooIn, ws: Workspace, services: Svc) -> WooOut:
    """Checks the key, links the store and takes the products from it now. Then our orders are
    written there, and it's synced every few minutes."""
    return woo_out(await services.woo.link(ws, body.url, body.key, body.secret))


@router.get("/api/agent/woo")
async def get_woo(ws: Workspace, services: Svc) -> WooOut:
    return woo_out(await services.woo.get(ws))


@router.post("/api/agent/woo/sync")
async def sync_woo(ws: Workspace, services: Svc) -> WooOut:
    """Writes the orders that changed and reads the products now."""
    return woo_out(await services.woo.sync(ws))


@router.delete("/api/agent/woo", status_code=status.HTTP_204_NO_CONTENT)
async def unlink_woo(ws: Workspace, services: Svc) -> None:
    """Stops syncing. The list stays and can be edited here again."""
    await services.woo.unlink(ws)
