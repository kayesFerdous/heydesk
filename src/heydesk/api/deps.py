import csv
import io
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any

from fastapi import Depends, Query, Request, Response
from fastapi.routing import APIRoute
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from heydesk.errors import Forbidden, HeyDeskError, NotFound, Unauthorized
from heydesk.services import Services
from heydesk.services.users import CurrentUser

bearer = HTTPBearer(auto_error=False)  # Swagger's "Authorize" button takes the token
SESSION_COOKIE = "heydesk_session"  # the browser's copy of the token; page scripts can't read it

# An admin names the business they're acting in; an owner's is always their own
BUSINESS_HEADER = "X-Business"


class BusinessRequired(HeyDeskError):
    code = "business_required"


def client_ip(request: Request) -> str:
    """The caller's address, for the rate limits. Behind a proxy, uvicorn takes the real
    address from the proxy's X-Forwarded-For header, if the proxy is on this machine."""
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


PAGE = 100  # the most rows one list request returns
# ponytail: an export is the newest 10,000 rows in one file; page it if a business has more
EXPORT_ROWS = 10_000
FORMULA = ("=", "+", "-", "@", "\t", "\r")  # a spreadsheet runs a cell that starts with these


def phone_cell(phone: str) -> str:
    """01812345678 as "01812-345678": a spreadsheet reads the digits alone as a number and
    drops the 0."""
    return f"{phone[:5]}-{phone[5:]}" if len(phone) == 11 and phone.isdigit() else phone


def amount_cell(minor: int) -> int | float:
    """৳1,045.50 as 1045.5, and ৳3,200 as 3200: a number a spreadsheet adds up."""
    return minor // 100 if minor % 100 == 0 else minor / 100


def csv_file(name: str, columns: list[str], rows: list[list[Any]]) -> Response:
    """A list as a file a spreadsheet opens: UTF-8 with a BOM, so Excel reads Bangla. A
    visitor's words that start like a formula get a ' first, so they stay words."""

    def cell(value: Any) -> Any:
        return f"'{value}" if isinstance(value, str) and value.startswith(FORMULA) else value

    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(columns)
    writer.writerows([cell(v) for v in row] for row in rows)
    return Response(
        "\ufeff" + out.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}.csv"'},
    )


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
type User = Annotated[CurrentUser, Depends(get_current_user)]
type Admin = Annotated[CurrentUser, Depends(admin_only)]
type Paged = Annotated[Page, Depends(page)]
type ClientIp = Annotated[str, Depends(client_ip)]


class StatusIn(BaseModel):
    status: str  # where it may go depends on what it is: see the 409 message


class ImportIn(BaseModel):
    """A CSV file of products."""

    csv: str = Field(
        max_length=2_000_000, description="The file's text: a header row, then one per row"
    )
    dry_run: bool = Field(default=False, description="Check the file and show what would be saved")
    mapping: dict[str, str] | None = Field(
        default=None,
        description="Our field → the file's column, as the owner checked it after /suggest "
        ". Without it, the file's columns are matched to ours by name",
    )


class SuggestIn(BaseModel):
    csv: str = Field(max_length=2_000_000, description="The file's text, as for the import")


class ImportOut[T](BaseModel):
    saved: bool
    count: int
    preview: list[T]  # the first few, as they'll be saved
    added: int | None = None  # a shop's import: how many were new; the rest were updated
