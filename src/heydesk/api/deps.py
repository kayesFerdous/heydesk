from typing import Annotated

from fastapi import Depends, Request
from fastapi.routing import APIRoute
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from heydesk.errors import HeyDeskError, NotFound, Unauthorized
from heydesk.services import Services
from heydesk.services.users import Principal

bearer = HTTPBearer(auto_error=False)  # Swagger's "Authorize" button takes the token

# An admin names the business they're acting in (D23); an owner's is always their own
BUSINESS_HEADER = "X-Business"


class BusinessRequired(HeyDeskError):
    code = "business_required"


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
) -> str:
    """The business every business route is scoped by (rule 6).

    An owner: their own, whatever the request says. An admin: the one in the X-Business
    header, after the database confirms they're still an admin. An admin's writes, and
    their reads of one thing (a conversation), go to the audit log.
    """
    if not principal.is_admin:
        assert principal.workspace_id is not None  # an owner's token always has one
        return principal.workspace_id
    if not await services.users.still_admin(principal):
        raise Unauthorized("Log in again.")
    business = request.headers.get(BUSINESS_HEADER, "")
    if not business:
        raise BusinessRequired(f"Pick a business first ({BUSINESS_HEADER} header).")
    await services.admin.require_business(business)
    if request.method != "GET" or request.path_params:
        route = request.scope.get("route")
        path = route.path if isinstance(route, APIRoute) else request.url.path
        target = ",".join(f"{k}={v}" for k, v in request.path_params.items())
        await services.admin.audit(principal.user_id, business, f"{request.method} {path}", target)
    return business


async def admin_only(
    services: Annotated[Services, Depends(get_services)],
    principal: Annotated[Principal, Depends(current_user)],
) -> Principal:
    """404 for everyone else: an owner can't even learn these routes exist."""
    if not await services.users.still_admin(principal):
        raise NotFound("Not found")
    return principal


type Svc = Annotated[Services, Depends(get_services)]
type Workspace = Annotated[str, Depends(current_workspace)]
type User = Annotated[Principal, Depends(current_user)]
type Admin = Annotated[Principal, Depends(admin_only)]


class StatusIn(BaseModel):
    status: str  # where it may go depends on what it is: see the 409 message


class ImportIn(BaseModel):
    """A CSV file for a desk's list (the menu, the doctors, the products)."""

    csv: str = Field(
        max_length=2_000_000, description="The file's text: a header row, then one per row"
    )
    dry_run: bool = Field(default=False, description="Check the file and show what would be saved")


class ImportOut[T](BaseModel):
    saved: bool
    count: int
    preview: list[T]  # the first few, as they'll be saved
