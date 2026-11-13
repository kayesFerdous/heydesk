import asyncio
import logging
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
from heydesk.db import make_engine, use_secrets_key, utcnow
from heydesk.errors import register_exception_handlers
from heydesk.logs import RequestLogMiddleware, configure_logging
from heydesk.ports.embedder import Embedder
from heydesk.ports.knowledge_store import KnowledgeStore
from heydesk.ports.llm import LanguageModel
from heydesk.ports.messenger import MessengerApi
from heydesk.ports.payments import Gateways
from heydesk.ports.photo_index import PhotoIndex
from heydesk.ports.telegram import TelegramApi
from heydesk.ports.whatsapp import WhatsAppApi
from heydesk.services.background import run_every, run_once_after
from heydesk.wiring import (
    build_embedder,
    build_knowledge_store,
    build_llm,
    build_photo_index,
    build_qdrant,
    build_redis,
    build_services,
)

log = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    *,
    llm: LanguageModel | None = None,
    embedder: Embedder | None = None,
    knowledge_store: KnowledgeStore | None = None,
    photo_index: PhotoIndex | None = None,
    telegram: TelegramApi | None = None,
    messenger: MessengerApi | None = None,
    whatsapp: WhatsAppApi | None = None,
    payments: Gateways | None = None,
    now: Callable[[], datetime] = utcnow,
) -> FastAPI:
    settings = settings or Settings()
    configure_logging(settings.LOG_LEVEL, settings.LOG_JSON)
    use_secrets_key(settings.secrets_key)
    dev = settings.APP_ENV == "development"

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        engine, sessions = make_engine(settings.DATABASE_URL)
        app.state.settings = settings
        app.state.engine = engine
        app.state.sessions = sessions
        store, photos = knowledge_store, photo_index
        if store is None or photos is None:  # one Qdrant client for both
            qdrant = build_qdrant(settings)
            store = store or build_knowledge_store(settings, qdrant)
            photos = photos or build_photo_index(settings, qdrant)
        redis = build_redis(settings)
        app.state.services = build_services(
            sessions,
            llm or build_llm(settings),
            embedder or build_embedder(settings),
            store,
            photos,
            settings,
            now,
            telegram=telegram,
            messenger=messenger,
            whatsapp=whatsapp,
            payments=payments,
            redis=redis,
        )
        if dev:
            try:
                agent = await app.state.services.agents.ensure_dev_agent(settings.DEV_AGENT_SEED)
            except OperationalError as error:
                raise RuntimeError(
                    "Couldn't set up the dev agent. Is the database migrated? "
                    "Run: uv run alembic upgrade head"
                ) from error
            app.state.dev_agent_key = agent.public_key
        services = app.state.services
        jobs = [
            run_every(settings.SHEET_SYNC_MINUTES * 60, services.sheets.sync_all),
            run_every(settings.WOO_SYNC_MINUTES * 60, services.woo.sync_all),
            run_every(settings.COURIER_SYNC_MINUTES * 60, services.courier.sync_all),
            run_every(settings.CALL_QUEUE_SECONDS, services.calls.check_queue),
            run_every(settings.SNOOZE_CHECK_SECONDS, services.inbox.wake_snoozed),
            run_every(settings.SNOOZE_CHECK_SECONDS, services.orders.remind_unconfirmed_orders),
            run_once_after(settings.SNOOZE_CHECK_SECONDS, services.channel_links.refresh_telegram),
        ]
        if redis is not None:  # other processes' inbox news, for this one's open pages
            jobs.append(services.live.run())
        loops = [asyncio.create_task(job) for job in jobs]
        log.info(
            "HeyDesk ready",
            extra={
                "env": settings.APP_ENV,
                "llm": settings.LLM_PROVIDER,
                "embedder": settings.EMBEDDER,
                "redis": redis is not None,
            },
        )
        yield
        for loop in loops:
            loop.cancel()
        if redis is not None:
            await redis.aclose()
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
    # Product photos and the ones visitors send, when they're on this server's disk and
    # not in R2; the folder is made on the first photo
    if not settings.R2_BUCKET:
        media = StaticFiles(directory=settings.MEDIA_DIR, check_dir=False)
        app.mount("/media", media, name="media")
    if dev:  # after the routes, so GET / is the page with the agent's key baked in
        app.mount("/", StaticFiles(directory=WEB_STATIC, html=True), name="web")
    return app


app = create_app()
