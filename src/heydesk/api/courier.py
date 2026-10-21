"""A shop's courier (D90): the owner links Steadfast or Pathao once, and the agent books each
confirmed order's parcel and follows it."""

from typing import Literal

from fastapi import APIRouter, status
from pydantic import BaseModel, Field

from heydesk.api.deps import Svc, Workspace
from heydesk.models import CourierLink
from heydesk.ports.courier import CourierAccount

router = APIRouter(tags=["courier"])


class CourierIn(BaseModel):
    kind: Literal["steadfast", "pathao"]
    key: str = Field(max_length=200, description="Steadfast's API key, or Pathao's client id")
    secret: str = Field(
        max_length=200, description="Steadfast's secret key, or Pathao's client secret"
    )
    username: str = Field(default="", max_length=200, description="Pathao: the merchant login")
    password: str = Field(default="", max_length=200, description="Pathao: its password")
    store_id: int | None = Field(
        default=None, description="Pathao: the pick-up store; empty: the first"
    )
    sandbox: bool = Field(default=False, description="Pathao's sandbox, for trying it out")


class CourierOut(BaseModel):
    kind: str
    store_id: int | None
    sandbox: bool
    problem: str  # empty: fine. Otherwise what went wrong, for the owner


def courier_out(link: CourierLink) -> CourierOut:
    return CourierOut(
        kind=link.kind, store_id=link.store_id, sandbox=link.sandbox, problem=link.problem
    )


@router.put("/api/agent/courier")
async def link_courier(body: CourierIn, ws: Workspace, services: Svc) -> CourierOut:
    """Checks the keys with the courier and links it. From then on, each confirmed order is
    booked there, and its status is followed."""
    given = CourierAccount(**body.model_dump())
    return courier_out(await services.courier.link(ws, given))


@router.get("/api/agent/courier")
async def get_courier(ws: Workspace, services: Svc) -> CourierOut:
    return courier_out(await services.courier.get(ws))


@router.post("/api/agent/courier/sync")
async def sync_courier(ws: Workspace, services: Svc) -> CourierOut:
    """Books the confirmed orders and reads the parcels' status now."""
    return courier_out(await services.courier.sync(ws))


@router.delete("/api/agent/courier", status_code=status.HTTP_204_NO_CONTENT)
async def unlink_courier(ws: Workspace, services: Svc) -> None:
    """Stops booking. Parcels already booked stay with the courier."""
    await services.courier.unlink(ws)
