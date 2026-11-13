"""The owner's view of the calls that confirm shop orders, and the queue's buttons."""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from heydesk.api.deps import Svc, WorkspaceId
from heydesk.models.confirmation_call import QUEUED

router = APIRouter(tags=["calls"])


class CallOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    order_id: str
    code: str  # the order's: "O-104"
    name: str
    phone: str
    status: str  # queued → ringing → talking → done
    # How it ended: confirmed, cancelled, no_answer, no_decision, skipped (the order moved on
    # first) or stopped (the owner cancelled it). Empty until it's done
    outcome: str
    tries: int  # rings so far
    next_try_at: datetime  # when a queued call rings, inside the calling hours
    conversation_id: str | None  # the call's transcript, once answered
    created_at: datetime
    updated_at: datetime
    place: int | None = None  # a queued call's place in line: 1 is next


class CallIn(BaseModel):
    order_id: str


@router.get("/api/calls")
async def calls(
    services: Svc,
    workspace_id: WorkspaceId,
    view: Literal["waiting", "done"] = "waiting",
    order_id: str | None = None,
) -> list[CallOut]:
    """`waiting`: the calls ringing or talking, then the queue in the order it's called, each
    with its `place`. `done`: the last 100, newest first. With `order_id`: that order's calls,
    newest first."""
    found = await services.calls.find(workspace_id, view, order_id)
    out = [CallOut.model_validate(c) for c in found]
    if view == "waiting" and not order_id:
        queued = [c for c in out if c.status == QUEUED]
        for place, call in enumerate(queued, start=1):
            call.place = place
    return out


@router.post("/api/calls", status_code=201)
async def call_order(body: CallIn, services: Svc, workspace_id: WorkspaceId) -> CallOut:
    """Call a new order to confirm it, first in the queue. 409 while calls are off, or when the
    order isn't new or already has a call waiting."""
    return CallOut.model_validate(await services.calls.call_order(workspace_id, body.order_id))


@router.post("/api/calls/{call_id}/now")
async def call_now(call_id: str, services: Svc, workspace_id: WorkspaceId) -> CallOut:
    """A queued call moves to the front and is due now."""
    return CallOut.model_validate(await services.calls.call_now(workspace_id, call_id))


@router.delete("/api/calls/{call_id}")
async def stop_call(call_id: str, services: Svc, workspace_id: WorkspaceId) -> CallOut:
    """Cancels a queued call (`stopped`); the order stays as it is."""
    return CallOut.model_validate(await services.calls.stop(workspace_id, call_id))
