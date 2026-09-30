from fastapi import FastAPI

from heydesk.api import health


def include_routers(app: FastAPI) -> None:
    app.include_router(health.router)
