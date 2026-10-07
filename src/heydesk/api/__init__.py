from fastapi import FastAPI

from heydesk.api import (
    admin,
    agents,
    auth,
    chat,
    conversations,
    dev,
    documents,
    health,
    telegram,
    voice,
)
from heydesk.desks.clinic import api as clinic
from heydesk.desks.restaurant import api as restaurant


def include_routers(app: FastAPI, *, dev_page: bool = False) -> None:
    app.include_router(health.router)
    app.include_router(chat.router)
    app.include_router(auth.router)
    app.include_router(admin.router)
    app.include_router(agents.router)
    app.include_router(documents.router)
    app.include_router(conversations.router)
    app.include_router(telegram.router)
    app.include_router(restaurant.router)  # each desk's own routes
    app.include_router(clinic.router)
    if dev_page:
        app.include_router(dev.router)
        app.include_router(voice.router)  # dev only until the voice channel is done
