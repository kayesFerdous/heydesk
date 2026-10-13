"""A restaurant's own routes: its menu, and the orders the agent took."""

from datetime import datetime
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from heydesk.api.deps import ImportIn, ImportOut, Paged, StatusIn, SuggestIn, Svc, Workspace
from heydesk.desks.csv_import import Suggestion
from heydesk.desks.restaurant.models import Order
from heydesk.desks.restaurant.store import RestaurantStore
from heydesk.desks.restaurant.values import MENU_COLUMNS, MenuItem, menu_from_csv

router = APIRouter(tags=["restaurant"])


async def restaurant_store(services: Svc, ws: Workspace) -> RestaurantStore:
    """404 unless the business's agent is a restaurant."""
    return cast(RestaurantStore, await services.agents.store(ws, "restaurant"))


type Restaurant = Annotated[RestaurantStore, Depends(restaurant_store)]


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    conversation_id: str | None
    number: int  # what the visitor was told
    status: str  # new → ready → collected, or cancelled
    name: str
    phone: str
    lines: list[dict[str, Any]]  # {code, name, quantity, price}
    total: int  # minor units
    currency: str
    address: str  # empty: pickup
    delivery_charge: int  # in `total`
    paid: int  # paid online first (D48)
    payment_ref: str  # the payment gateway's id for it
    pickup_at: datetime  # UTC
    note: str
    created_at: datetime
    updated_at: datetime


@router.get("/api/menu")
async def menu(store: Restaurant) -> list[MenuItem]:
    return await store.menu()


@router.put("/api/menu")
async def replace_menu(body: list[MenuItem], store: Restaurant) -> list[MenuItem]:
    """The menu becomes exactly these dishes, in this order. Live from the next message."""
    await store.replace_menu(body)
    return body


@router.get("/api/orders")
async def orders(
    store: Restaurant, page: Paged, status: str | None = None, q: str | None = None
) -> list[OrderOut]:
    """Newest first, a page at a time (`limit`, `before`). `q` matches the number, the name
    or the phone."""
    found = await store.find(Order, status, q, limit=page.limit, before=page.before)
    return [OrderOut.model_validate(o) for o in found]


@router.get("/api/orders/{order_id}")
async def order(order_id: str, store: Restaurant) -> OrderOut:
    return OrderOut.model_validate(await store.get(Order, order_id))


@router.patch("/api/orders/{order_id}")
async def set_status(order_id: str, body: StatusIn, store: Restaurant) -> OrderOut:
    return OrderOut.model_validate(await store.set_status(Order, order_id, body.status))


@router.post("/api/menu/import/suggest")
async def suggest_menu_columns(body: SuggestIn, services: Svc, _: Restaurant) -> Suggestion:
    """The file's columns, a few rows and our fields, with the AI's guess at which column is
    which field. The owner checks it, then imports with that `mapping` (D53)."""
    return await services.imports.suggest(body.csv, MENU_COLUMNS)


@router.post("/api/menu/import")
async def import_menu(body: ImportIn, store: Restaurant) -> ImportOut[MenuItem]:
    """Replaces the menu with the CSV's rows. Problems come back per row, and nothing is
    saved until the whole file is clean. `dry_run` only checks it."""
    found = menu_from_csv(body.csv, body.mapping)
    if not body.dry_run:
        await store.replace_menu(found)
    return ImportOut[MenuItem](saved=not body.dry_run, count=len(found), preview=found[:5])
