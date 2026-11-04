from fastapi import FastAPI

from heydesk.api import (
    admin,
    agents,
    auth,
    billing,
    calls,
    chat,
    conversations,
    courier,
    dev,
    documents,
    health,
    members,
    messenger,
    payments,
    sheets,
    telegram,
    voice,
    whatsapp,
    woo,
)
from heydesk.shop import api as shop


def include_routers(app: FastAPI, *, dev_page: bool = False) -> None:
    app.include_router(health.router)
    app.include_router(chat.router)
    app.include_router(auth.router)
    app.include_router(admin.router)
    app.include_router(agents.router)
    app.include_router(documents.router)
    app.include_router(conversations.router)
    app.include_router(courier.router)
    app.include_router(members.router)  # the business's moderators
    app.include_router(calls.router)  # the shop's calls to confirm orders
    app.include_router(telegram.router)
    app.include_router(messenger.router)
    app.include_router(whatsapp.router)
    app.include_router(payments.router)
    app.include_router(billing.router)  # paying HeyDesk for a plan
    app.include_router(sheets.router)
    app.include_router(woo.router)
    app.include_router(shop.router)
    if dev_page:
        app.include_router(dev.router)
        app.include_router(voice.router)  # dev only until the voice channel is done
