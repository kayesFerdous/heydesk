"""A shop's list kept in its WooCommerce store, and our orders written there: the owner
links it once with a Read/Write key, and the store's webhooks tell us when to sync."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Header, Request, status
from pydantic import BaseModel, Field

from heydesk.api.deps import Svc, Workspace
from heydesk.errors import NotFound
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
async def link_woo(body: WooIn, workspace_id: Workspace, services: Svc) -> WooOut:
    """Checks the key, links the store and takes the products from it now. Then our orders are
    written there, and it's synced when its webhooks say something changed."""
    return woo_out(await services.woo.link(workspace_id, body.url, body.key, body.secret))


@router.get("/api/agent/woo")
async def get_woo(workspace_id: Workspace, services: Svc) -> WooOut:
    return woo_out(await services.woo.get(workspace_id))


@router.post("/api/agent/woo/sync")
async def sync_woo(workspace_id: Workspace, services: Svc) -> WooOut:
    """Writes the orders that changed and reads the products now."""
    return woo_out(await services.woo.sync(workspace_id))


@router.delete("/api/agent/woo", status_code=status.HTTP_204_NO_CONTENT)
async def unlink_woo(workspace_id: Workspace, services: Svc) -> None:
    """Stops syncing. The list stays and can be edited here again."""
    await services.woo.unlink(workspace_id)


@router.post("/api/woo/hook/{workspace_id}", include_in_schema=False)
async def woo_webhook(
    workspace_id: str,
    request: Request,
    services: Svc,
    signature: Annotated[str, Header(alias="X-WC-Webhook-Signature")] = "",
) -> dict[str, bool]:
    """A linked store says a product or an order changed: we sync in a moment. Woo's ping when
    a webhook is made has no signature and needs only a 200."""
    if not signature:
        return {"ok": True}
    if not await services.woo.hook_signed(workspace_id, await request.body(), signature):
        raise NotFound("Not found")
    services.woo.sync_soon(workspace_id)
    return {"ok": True}
