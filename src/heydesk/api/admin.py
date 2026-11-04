"""The platform admin's routes. Every one needs `Admin`: a 404 for owners.
To act inside a business, an admin uses the ordinary routes with the X-Business header."""

from dataclasses import asdict
from datetime import date, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field, model_validator

from heydesk.api.auth import ROLES, Role
from heydesk.api.deps import Admin, Paged, Svc
from heydesk.services.users import temporary_password

router = APIRouter(prefix="/api/admin", tags=["admin"])


class BusinessRowOut(BaseModel):
    id: str
    name: str
    status: Literal["active", "suspended"]
    suspended_reason: str  # for admins only; empty while active
    kind: str | None  # "shop"; None: no agent yet
    owner_email: str | None
    conversations: int
    last_activity_at: datetime | None
    created_at: datetime
    plan: str
    paid_until: datetime


class OwnerOut(BaseModel):
    email: str
    created_at: datetime


class ChannelOut(BaseModel):
    kind: Literal["telegram", "messenger", "whatsapp"]
    address: str  # the bot's username; the Page's id; the number's id
    created_at: datetime


class SheetOut(BaseModel):
    synced_at: datetime | None  # the last good read
    problem: str  # empty: fine


class TotalsOut(BaseModel):
    turns: int
    conversations: int  # started in those days
    input_tokens: int
    cached_tokens: int  # of the input; cached / input is the cache's hit rate
    output_tokens: int
    cost_micros: int  # millionths of a dollar; priced providers only (Groq)
    failed: int  # turns that ended with the polite line
    handed_off: int
    first_reply_ms: int | None  # the median first reply
    slow_first_reply_ms: int | None  # the 95th percentile


class BusinessOut(BaseModel):
    id: str
    name: str
    status: Literal["active", "suspended"]
    suspended_reason: str  # for admins only; empty while active
    kind: str | None  # "shop"; None: no agent yet
    agent_name: str | None
    public_key: str | None  # the chat link is /chat/<public_key>
    created_at: datetime
    owners: list[OwnerOut]
    channels: list[ChannelOut]
    payments: Literal["sandbox", "live"] | None  # None: not connected
    documents: int
    sheet: SheetOut | None
    conversations: int
    conversations_7_days: int
    waiting: int  # handed to a person and not yet answered
    last_activity_at: datetime | None
    usage: TotalsOut  # its turns in the last 7 days


class DayOut(BaseModel):
    day: date  # Dhaka's
    turns: int
    conversations: int
    cost_micros: int
    failed: int
    handed_off: int


class BusyOut(BaseModel):
    id: str
    name: str
    turns: int
    cost_micros: int


class OverviewOut(BaseModel):
    days: int
    today: TotalsOut
    period: TotalsOut
    per_day: list[DayOut]  # oldest first
    busiest: list[BusyOut]  # the most turns first, up to 5


class FailedTurnOut(BaseModel):
    id: str
    at: datetime
    workspace_id: str
    business_name: str
    business_status: Literal["active", "suspended"]
    conversation_id: str
    channel: str
    llm: str
    error: str


class BusinessStatusIn(BaseModel):
    status: Literal["active", "suspended"]
    reason: str = Field(default="", max_length=300)  # needed to suspend

    @model_validator(mode="after")
    def _a_suspension_says_why(self) -> "BusinessStatusIn":
        if self.status == "suspended" and not self.reason.strip():
            raise ValueError("Say why the business is suspended.")
        return self


class UserRowOut(BaseModel):
    id: str
    email: str
    role: Role
    workspace_id: str | None
    business_name: str | None
    created_at: datetime


class TemporaryPasswordOut(BaseModel):
    password: str  # shown once


class AuditOut(BaseModel):
    id: str
    actor_email: str
    workspace_id: str | None
    action: str
    target: str
    at: datetime


@router.get("/overview")
async def overview(
    admin: Admin, services: Svc, days: Annotated[int, Query(ge=1, le=30)] = 7
) -> OverviewOut:
    """What every agent did today and in the last `days` days, from the saved turns."""
    return OverviewOut.model_validate(asdict(await services.admin.overview(days)))


@router.get("/failed-turns")
async def failed_turns(
    admin: Admin, services: Svc, page: Paged, workspace_id: str | None = None
) -> list[FailedTurnOut]:
    """Turns that ended with the polite line, newest first, a page at a time."""
    rows = await services.admin.failed_turns(workspace_id, limit=page.limit, before=page.before)
    return [FailedTurnOut.model_validate(asdict(row)) for row in rows]


@router.get("/businesses")
async def businesses(admin: Admin, services: Svc) -> list[BusinessRowOut]:
    return [BusinessRowOut.model_validate(asdict(row)) for row in await services.admin.businesses()]


@router.get("/businesses/{workspace_id}")
async def business(workspace_id: str, admin: Admin, services: Svc) -> BusinessOut:
    """One business at a glance: counts and connections, no customer's words."""
    return BusinessOut.model_validate(asdict(await services.admin.business(workspace_id)))


@router.patch("/businesses/{workspace_id}", status_code=204)
async def business_status(
    workspace_id: str, body: BusinessStatusIn, admin: Admin, services: Svc
) -> None:
    """Suspended: the chat link says the assistant is unavailable; the owner can still log in."""
    await services.admin.set_business_status(workspace_id, body.status, body.reason)
    reason = body.reason.strip() if body.status == "suspended" else ""
    await services.admin.audit(admin.user_id, workspace_id, f"business {body.status}", reason)


class PlanIn(BaseModel):
    plan: Literal["trial", "starter", "growth", "pro"]
    paid_until: datetime  # the last paid day


@router.put("/businesses/{workspace_id}/plan", status_code=204)
async def business_plan(workspace_id: str, body: PlanIn, admin: Admin, services: Svc) -> None:
    """A plan set by hand, for a payment made another way (bKash to our number)."""
    await services.billing.set_plan(workspace_id, body.plan, body.paid_until)
    target = f"{body.plan} until {body.paid_until:%Y-%m-%d}"
    await services.admin.audit(admin.user_id, workspace_id, "business plan", target)


@router.get("/users")
async def users(admin: Admin, services: Svc) -> list[UserRowOut]:
    return [
        UserRowOut(
            id=user.id,
            email=user.email,
            role=ROLES[user.role],
            workspace_id=user.workspace_id,
            business_name=name,
            created_at=user.created_at,
        )
        for user, name in await services.admin.users()
    ]


@router.post("/users/{user_id}/password")
async def reset_password(user_id: str, admin: Admin, services: Svc) -> TemporaryPasswordOut:
    """For an owner who forgot theirs: a temporary password, shown once."""
    password = temporary_password()
    await services.users.set_password(user_id, password)
    await services.admin.audit(admin.user_id, None, "password reset", f"user_id={user_id}")
    return TemporaryPasswordOut(password=password)


@router.get("/audit")
async def audit(
    admin: Admin, services: Svc, page: Paged, workspace_id: str | None = None
) -> list[AuditOut]:
    """Newest first, a page at a time (`limit`, `before` an `at`)."""
    return [
        AuditOut(
            id=event.id,
            actor_email=email,
            workspace_id=event.workspace_id,
            action=event.action,
            target=event.target,
            at=event.at,
        )
        for event, email in await services.admin.audit_log(
            workspace_id, limit=page.limit, before=page.before
        )
    ]
