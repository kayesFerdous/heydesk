from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from heydesk.api import include_routers
from heydesk.config import Settings
from heydesk.db import make_engine
from heydesk.errors import register_exception_handlers
from heydesk.logs import RequestLogMiddleware, configure_logging
from heydesk.ports.decider import Decider
from heydesk.ports.llm import LanguageModel
from heydesk.wiring import build_decider, build_llm, build_services

WEB_STATIC = Path(__file__).parent / "web" / "static"


def create_app(
    settings: Settings | None = None,
    *,
    llm: LanguageModel | None = None,
    decider: Decider | None = None,
) -> FastAPI:
    settings = settings or Settings()
    configure_logging(settings.LOG_LEVEL, settings.LOG_JSON)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        engine, sessions = make_engine(settings.DATABASE_URL)
        app.state.settings = settings
        app.state.sessions = sessions
        app.state.services = build_services(
            llm or build_llm(settings), decider or build_decider(settings), settings
        )
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
    if settings.APP_ENV == "development":
        app.mount("/", StaticFiles(directory=WEB_STATIC, html=True), name="web")  # the minimal page
    return app


app = create_app()
