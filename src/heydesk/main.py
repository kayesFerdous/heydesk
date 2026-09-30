from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from heydesk.api import include_routers
from heydesk.config import Settings
from heydesk.db import make_engine
from heydesk.errors import register_exception_handlers
from heydesk.logs import RequestLogMiddleware, configure_logging


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    configure_logging(settings.LOG_LEVEL, settings.LOG_JSON)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        engine, sessions = make_engine(settings.DATABASE_URL)
        app.state.settings = settings
        app.state.sessions = sessions
        yield
        await engine.dispose()

    app = FastAPI(title="HeyDesk", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RequestLogMiddleware)  # outermost: logs every request, even CORS rejections
    register_exception_handlers(app)
    include_routers(app)
    return app


app = create_app()
