from fastapi import Request

from heydesk.services import Services


def get_services(request: Request) -> Services:
    services: Services = request.app.state.services
    return services
