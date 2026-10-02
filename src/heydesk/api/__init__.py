from fastapi import FastAPI

from heydesk.api import chat, dev, health, voice


def include_routers(app: FastAPI, *, dev_page: bool = False) -> None:
    app.include_router(health.router)
    app.include_router(chat.router)
    if dev_page:
        app.include_router(dev.router)
        app.include_router(voice.router)  # dev only until the voice channel is done
