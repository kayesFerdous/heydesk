from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated

from fastapi import Depends, Query, Request
from fastapi.routing import APIRoute
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from heydesk.errors import Forbidden, HeyDeskError, NotFound, Unauthorized
from heydesk.services import Services
from heydesk.services.users import Principal

bearer = HTTPBearer(auto_error=False)  # Swagger's "Authorize" button takes the token

# An admin names the business they're acting in (D23); an owner's is always their own
BUSINESS_HEADER = "X-Business"


class BusinessRequired(HeyDeskError):
    code = "business_required"


def client_ip(request: Request) -> str:
    """The caller's address, for the rate limits (D52). Behind a proxy, uvicorn takes the real
    address from the proxy's X-Forwarded-For header, if the proxy is on this machine."""
    return request.client.host if request.client else "unknown"


def get_services(request: Request) -> Services:
    services: Services = request.app.state.services
    return services


def current_user(
    services: Annotated[Services, Depends(get_services)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> Principal:
    """The logged-in user (`Authorization: Bearer <token>`)."""
    if credentials is None:
        raise Unauthorized("Log in first, and send the token as 'Authorization: Bearer …'.")
    return services.users.principal(credentials.credentials)


async def current_workspace(
    request: Request,
    services: Annotated[Services, Depends(get_services)],
    principal: Annotated[Principal, Depends(current_user)],
) -> AsyncIterator[str]:
    """The business every business route is scoped by (rule 6).

    An owner: their own, whatever the request says. A moderator: theirs too, after the
    database confirms they're still on the team, and never changing its settings (D97). An
    admin: the one in the X-Business header, after the database confirms they're still an
    admin. An admin's writes, and their reads of one thing (a conversation), go to the audit
    log; an owner's and a moderator's writes that succeed go to the business's activity.
    """
    route = request.scope.get("route")
    path = route.path if isinstance(route, APIRoute) else request.url.path
    target = ",".join(f"{k}={v}" for k, v in request.path_params.items())
    action = f"{request.method} {path}"
    if principal.is_admin:
        yield await _admin_business(request, services, principal, action, target)
        return
    assert principal.workspace_id is not None  # an owner's or moderator's token always has one
    if principal.role == "moderator":
        if not await services.users.still_moderator(principal):
            raise Unauthorized("Log in again.")  # removed from the team: out at once
        if owners_only(request.method, path):
            raise Forbidden("Only the business's owner can do this.")
    yield principal.workspace_id
    if request.method != "GET":  # reached only when the route didn't raise
        await services.members.record(principal, action, target)


def owners_only(method: str, path: str) -> bool:
    """The team, the Telegram bot (its link makes you the one who gets the alerts) and any
    change to the agent's settings are the owner's. A moderator gets the rest: the inbox, the
    records, the lists, and reading the settings, such as whether a sheet is linked (D97)."""
    if path.startswith(("/api/team", "/api/agent/telegram")):
        return True
    return path.startswith("/api/agent") and method != "GET"


async def _admin_business(
    request: Request, services: Services, principal: Principal, action: str, target: str
) -> str:
    if not await services.users.still_admin(principal):
        raise Unauthorized("Log in again.")
    business = request.headers.get(BUSINESS_HEADER, "")
    if not business:
        raise BusinessRequired(f"Pick a business first ({BUSINESS_HEADER} header).")
    await services.admin.require_business(business)
    if request.method != "GET" or request.path_params:
        await services.admin.audit(principal.user_id, business, action, target)
    return business


async def admin_only(
    services: Annotated[Services, Depends(get_services)],
    principal: Annotated[Principal, Depends(current_user)],
) -> Principal:
    """404 for everyone else: an owner can't even learn these routes exist."""
    if not await services.users.still_admin(principal):
        raise NotFound("Not found")
    return principal


PAGE = 100  # the most rows one list request returns


@dataclass(frozen=True)
class Page:
    """Which slice of a newest-first list to return: up to `limit` rows, all older than
    `before`. To see more, ask again with `before` set to the last row's time."""

    limit: int = PAGE
    before: datetime | None = None


def page(
    limit: Annotated[int, Query(ge=1, le=PAGE)] = PAGE, before: datetime | None = None
) -> Page:
    return Page(limit, before)


type Svc = Annotated[Services, Depends(get_services)]
# "function": the activity line is written once the route returns, before the response
type Workspace = Annotated[str, Depends(current_workspace, scope="function")]
type User = Annotated[Principal, Depends(current_user)]
type Admin = Annotated[Principal, Depends(admin_only)]
type Paged = Annotated[Page, Depends(page)]
type ClientIp = Annotated[str, Depends(client_ip)]


class StatusIn(BaseModel):
    status: str  # where it may go depends on what it is: see the 409 message


class ImportIn(BaseModel):
    """A CSV file for a desk's list (the menu, the doctors, the products)."""

    csv: str = Field(
        max_length=2_000_000, description="The file's text: a header row, then one per row"
    )
    dry_run: bool = Field(default=False, description="Check the file and show what would be saved")
    mapping: dict[str, str] | None = Field(
        default=None,
        description="Our field → the file's column, as the owner checked it after /suggest "
        "(D53). Without it, the file's columns are matched to ours by name",
    )


class SuggestIn(BaseModel):
    csv: str = Field(max_length=2_000_000, description="The file's text, as for the import")


class ImportOut[T](BaseModel):
    saved: bool
    count: int
    preview: list[T]  # the first few, as they'll be saved
