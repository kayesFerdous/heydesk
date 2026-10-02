from fastapi import FastAPI

from heydesk.api import agents, chat, dev, health, presets, records, voice


def include_routers(app: FastAPI, *, dev_page: bool = False) -> None:
    app.include_router(health.router)
    app.include_router(chat.router)
    app.include_router(presets.router)
    app.include_router(agents.router)
    app.include_router(records.router)
    if dev_page:
        app.include_router(dev.router)
        app.include_router(voice.router)  # dev only until the voice channel is done
