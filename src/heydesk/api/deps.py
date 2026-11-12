from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated

from fastapi import Depends, Query, Request
from fastapi.routing import APIRoute
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from heydesk.errors import Forbidden, HeyDeskError, NotFound, Unauthorized
from heydesk.services import Services
from heydesk.services.users import CurrentUser
from heydesk.shop.records import PAGE  # the most rows one list request returns
from heydesk.shop.store import ShopStore

bearer = HTTPBearer(auto_error=False)  # Swagger's "Authorize" button takes the token
SESSION_COOKIE = "heydesk_session"  # the browser's copy of the token; page scripts can't read it

# An admin names the business they're acting in; an owner's is always their own
BUSINESS_HEADER = "X-Business"


class BusinessRequired(HeyDeskError):
    code = "business_required"


def client_ip(request: Request) -> str:
    """The caller's address, for the rate limits. Behind a proxy, uvicorn takes the real
    address from the proxy's X-Forwarded-For header, if it trusts the proxy: one on this
    machine, or on Render one in the private network (`FORWARDED_ALLOW_IPS` in render.yaml)."""
    return request.client.host if request.client else "unknown"


def get_services(request: Request) -> Services:
    services: Services = request.app.state.services
    return services


def get_current_user(
    request: Request,
    services: Annotated[Services, Depends(get_services)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> CurrentUser:
    """The logged-in user: the browser's session cookie, or `Authorization: Bearer <token>`
    for Swagger and scripts."""
    token = credentials.credentials if credentials else request.cookies.get(SESSION_COOKIE)
    if not token:
        raise Unauthorized("Log in first.")
    return services.users.user_from_token(token)


async def current_workspace(
    request: Request,
    services: Annotated[Services, Depends(get_services)],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
) -> AsyncIterator[str]:
    """The business every business route is scoped by (rule 6).

    An owner: their own, whatever the request says. A moderator: theirs too, after the
    database confirms they're still on the team, and never changing its settings. An
    admin: the one in the X-Business header, after the database confirms they're still an
    admin. An admin's writes, and their reads of one thing (a conversation), go to the audit
    log; an owner's and a moderator's writes that succeed go to the business's activity.
    """
    route = request.scope.get("route")
    path = route.path if isinstance(route, APIRoute) else request.url.path
    target = ",".join(f"{k}={v}" for k, v in request.path_params.items())
    action = f"{request.method} {path}"
    if current_user.is_admin:
        yield await _admin_business(request, services, current_user, action, target)
        return
    assert current_user.workspace_id is not None  # an owner's or moderator's token always has one
    if current_user.role == "moderator":
        if not await services.users.still_moderator(current_user):
            raise Unauthorized("Log in again.")  # removed from the team: out at once
        if owners_only(request.method, path):
            raise Forbidden("Only the business's owner can do this.")
    yield current_user.workspace_id
    if request.method != "GET":  # reached only when the route didn't raise
        await services.members.record(current_user, action, target)


def owners_only(method: str, path: str) -> bool:
    """The team, billing and any change to the agent's settings or channels are the
    owner's. A moderator gets the rest: the inbox, the records, the lists, and reading the
    settings and channels, but change none of them."""
    if path.startswith(("/api/team", "/api/billing")):
        return True
    return path.startswith("/api/agent") and method != "GET"


async def _admin_business(
    request: Request, services: Services, current_user: CurrentUser, action: str, target: str
) -> str:
    if not await services.users.still_admin(current_user):
        raise Unauthorized("Log in again.")
    business = request.headers.get(BUSINESS_HEADER, "")
    if not business:
        raise BusinessRequired(f"Pick a business first ({BUSINESS_HEADER} header).")
    await services.admin.require_business(business)
    if request.method != "GET" or request.path_params:
        await services.admin.audit(current_user.user_id, business, action, target)
    return business


async def admin_only(
    services: Annotated[Services, Depends(get_services)],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
) -> CurrentUser:
    """404 for everyone else: an owner can't even learn these routes exist."""
    if not await services.users.still_admin(current_user):
        raise NotFound("Not found")
    return current_user


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
# The id of the business (workspace) the request is for: a string, not the `Workspace` row.
# "function": the activity line is written once the route returns, before the response
type WorkspaceId = Annotated[str, Depends(current_workspace, scope="function")]
# Who is logged in: a `CurrentUser` from their token, not the `User` row
type LoggedInUser = Annotated[CurrentUser, Depends(get_current_user)]
type Admin = Annotated[CurrentUser, Depends(admin_only)]
type Paged = Annotated[Page, Depends(page)]
type ClientIp = Annotated[str, Depends(client_ip)]


async def shop_store(services: Svc, workspace_id: WorkspaceId) -> ShopStore:
    """The data of the business the request is for. 404 until the business has an agent."""
    return await services.agents.store(workspace_id)


type Shop = Annotated[ShopStore, Depends(shop_store)]


class StatusIn(BaseModel):
    status: str  # where it may go depends on what it is: see the 409 message
