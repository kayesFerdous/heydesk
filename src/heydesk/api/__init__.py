from fastapi import FastAPI

from heydesk.api import chat, health


def include_routers(app: FastAPI) -> None:
    app.include_router(health.router)
    app.include_router(chat.router)
