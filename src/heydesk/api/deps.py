from fastapi import Request

from heydesk.services import Services
from heydesk.services.agents import DEV_WORKSPACE_ID


def get_services(request: Request) -> Services:
    services: Services = request.app.state.services
    return services


def current_workspace() -> str:
    """The caller's workspace. The dev workspace until login (day 20)."""
    return DEV_WORKSPACE_ID
