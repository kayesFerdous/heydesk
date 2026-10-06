from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import OperationalError

from heydesk.api import include_routers
from heydesk.api.dev import WEB_STATIC
from heydesk.config import Settings
from heydesk.db import make_engine, utcnow
from heydesk.errors import register_exception_handlers
from heydesk.logs import RequestLogMiddleware, configure_logging
from heydesk.ports.decider import Decider
from heydesk.ports.embedder import Embedder
from heydesk.ports.knowledge_store import KnowledgeStore
from heydesk.ports.llm import LanguageModel
from heydesk.wiring import (
    build_decider,
    build_embedder,
    build_knowledge_store,
    build_llm,
    build_services,
)


def create_app(
    settings: Settings | None = None,
    *,
    llm: LanguageModel | None = None,
    decider: Decider | None = None,
    embedder: Embedder | None = None,
    knowledge_store: KnowledgeStore | None = None,
    now: Callable[[], datetime] = utcnow,
) -> FastAPI:
    settings = settings or Settings()
    configure_logging(settings.LOG_LEVEL, settings.LOG_JSON)
    dev = settings.APP_ENV == "development"

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        engine, sessions = make_engine(settings.DATABASE_URL)
        app.state.settings = settings
        app.state.engine = engine
        app.state.sessions = sessions
        app.state.services = build_services(
            sessions,
            llm or build_llm(settings),
            decider or build_decider(settings),
            embedder or build_embedder(settings),
            knowledge_store or build_knowledge_store(settings),
            settings,
            now,
        )
        if dev:
            try:
                agent = await app.state.services.agents.ensure_dev_agent(
                    settings.DEV_AGENT_KIND, settings.DEV_AGENT_SEED
                )
            except OperationalError as error:
                raise RuntimeError(
                    "Couldn't set up the dev agent. Is the database migrated? "
                    "Run: uv run alembic upgrade head"
                ) from error
            app.state.dev_agent_key = agent.public_key
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
    include_routers(app, dev_page=dev)
    if dev:  # after the routes, so GET / is the page with the agent's key baked in
        app.mount("/", StaticFiles(directory=WEB_STATIC, html=True), name="web")
    return app


app = create_app()
