"""A restaurant's own routes: its menu, and the orders the agent took."""

from datetime import datetime
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from heydesk.api.deps import StatusIn, Svc, Workspace
from heydesk.desks.restaurant.models import Order
from heydesk.desks.restaurant.store import RestaurantStore
from heydesk.desks.restaurant.values import MenuItem

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
    store: Restaurant, status: str | None = None, q: str | None = None
) -> list[OrderOut]:
    """Newest first. `q` matches the number, the name or the phone."""
    return [OrderOut.model_validate(o) for o in await store.find(Order, status, q)]


@router.get("/api/orders/{order_id}")
async def order(order_id: str, store: Restaurant) -> OrderOut:
    return OrderOut.model_validate(await store.get(Order, order_id))


@router.patch("/api/orders/{order_id}")
async def set_status(order_id: str, body: StatusIn, store: Restaurant) -> OrderOut:
    return OrderOut.model_validate(await store.set_status(Order, order_id, body.status))
