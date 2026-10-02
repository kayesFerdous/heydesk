from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from heydesk.errors import Unauthorized
from heydesk.services import Services

bearer = HTTPBearer(auto_error=False)  # Swagger's "Authorize" button takes the token


def get_services(request: Request) -> Services:
    services: Services = request.app.state.services
    return services


def current_workspace(
    services: Annotated[Services, Depends(get_services)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> str:
    """The workspace of the logged-in user (`Authorization: Bearer <token>`)."""
    if credentials is None:
        raise Unauthorized("Log in first, and send the token as 'Authorization: Bearer …'.")
    return services.users.workspace_of(credentials.credentials)
