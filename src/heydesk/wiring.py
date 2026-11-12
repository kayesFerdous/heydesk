"""Builds adapters from Settings and hands them to services as ports.

The only module that imports `heydesk.adapters`.
"""

from collections.abc import Callable
from datetime import datetime, timedelta

from qdrant_client import AsyncQdrantClient
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from heydesk.adapters.courier.fake import FakeCourier
from heydesk.adapters.courier.pathao import Pathao
from heydesk.adapters.courier.steadfast import Steadfast
from heydesk.adapters.embedder.fake import FakeEmbedder
from heydesk.adapters.embedder.gemini import GeminiEmbedder
from heydesk.adapters.knowledge.photos import QdrantPhotoIndex
from heydesk.adapters.knowledge.qdrant import QdrantKnowledgeStore
from heydesk.adapters.llm.fake import FakeLLM
from heydesk.adapters.llm.gigalogy import GigalogyLLM
from heydesk.adapters.llm.groq import GroqLLM
from heydesk.adapters.mailer import Brevo, ConsoleMailer
from heydesk.adapters.media import DiskMedia, R2Media, fetch_image
from heydesk.adapters.messenger.graph import MessengerGraph
from heydesk.adapters.payments.bkash import BkashCheckout
from heydesk.adapters.payments.fake import FakeBkash, FakePayments
from heydesk.adapters.payments.sslcommerz import SslCommerz
from heydesk.adapters.sheets import fetch_csv
from heydesk.adapters.telegram.bot import TelegramBot
from heydesk.adapters.transcriber.chorus import ChorusTranscriber
from heydesk.adapters.transcriber.deepgram import DeepgramTranscriber
from heydesk.adapters.transcriber.fake import FakeTranscriber
from heydesk.adapters.whatsapp.cloud import WhatsAppCloud
from heydesk.adapters.woo import WooRest
from heydesk.config import Settings
from heydesk.ports.courier import CourierApi
from heydesk.ports.embedder import Embedder
from heydesk.ports.knowledge_store import KnowledgeStore
from heydesk.ports.llm import LanguageModel
from heydesk.ports.mailer import Mailer
from heydesk.ports.media import FetchImage, MediaStore
from heydesk.ports.messenger import MessengerApi
from heydesk.ports.payments import Gateways
from heydesk.ports.photo_index import PhotoIndex
from heydesk.ports.sheets import FetchCsv
from heydesk.ports.telegram import TelegramApi
from heydesk.ports.transcriber import Transcriber
from heydesk.ports.whatsapp import WhatsAppApi
from heydesk.ports.woo import WooApi
from heydesk.services import Services
from heydesk.services.admin import AdminService
from heydesk.services.agents import AgentService
from heydesk.services.billing import BillingService
from heydesk.services.calls import CallService
from heydesk.services.channel_links import ChannelLinkService
from heydesk.services.channels import ChannelService
from heydesk.services.conversations import ConversationService
from heydesk.services.courier import CourierService
from heydesk.services.inbox import InboxService
from heydesk.services.knowledge import KnowledgeService
from heydesk.services.live import LiveHub, watch
from heydesk.services.members import MemberService
from heydesk.services.payments import PaymentService
from heydesk.services.photos import PhotoService
from heydesk.services.rate_limits import RateLimiter
from heydesk.services.sheets import SheetService
from heydesk.services.team import TeamService
from heydesk.services.users import UserService
from heydesk.services.visitors import VisitorService
from heydesk.services.woo import WooService


def build_llm(settings: Settings) -> LanguageModel:
    if settings.LLM_PROVIDER == "gigalogy":
        return GigalogyLLM(
            _required(settings, "GIGALOGY_PROJECT_KEY"),
            _required(settings, "GIGALOGY_API_KEY"),
            _required(settings, "GIGALOGY_PROFILE_ID"),
        )
    if settings.LLM_PROVIDER == "groq":
        return GroqLLM(_required(settings, "GROQ_API_KEY"))
    return FakeLLM(delay=0.04)


def _required(settings: Settings, name: str, because: str = "") -> str:
    value: str = getattr(settings, name)
    if not value:
        raise RuntimeError(
            f"{because or 'LLM_PROVIDER=' + settings.LLM_PROVIDER} needs {name} in .env"
        )
    return value


def build_embedder(settings: Settings) -> Embedder:
    if settings.EMBEDDER == "gemini":
        return GeminiEmbedder(
            _required(settings, "GEMINI_API_KEY", "EMBEDDER=gemini"),
            settings.EMBEDDING_MODEL,
            settings.EMBEDDING_DIMENSIONS,
        )
    return FakeEmbedder(settings.EMBEDDING_DIMENSIONS)  # words only: no meaning, no key


def build_qdrant(settings: Settings) -> AsyncQdrantClient:
    """Qdrant Cloud when `QDRANT_URL` is set; otherwise its local mode, in a folder. One client
    for the documents and the photos: local mode lets only one client open its folder."""
    if settings.QDRANT_URL:
        return AsyncQdrantClient(
            url=settings.QDRANT_URL, api_key=settings.QDRANT_API_KEY or None, timeout=20
        )
    return AsyncQdrantClient(path=settings.QDRANT_LOCAL_PATH)


def build_redis(settings: Settings) -> Redis | None:
    """One Redis client for the rate limits and the live inbox, when `REDIS_URL` is set. A Redis
    that doesn't answer in 2 seconds counts as down (the live inbox's wait for news is exempt)."""
    if not settings.REDIS_URL:
        return None
    return Redis.from_url(settings.REDIS_URL, socket_timeout=2, socket_connect_timeout=2)


def build_knowledge_store(settings: Settings, client: AsyncQdrantClient) -> KnowledgeStore:
    # The floor was measured on Gemini's scores; the fake embedder's mean something else
    floor = settings.KNOWLEDGE_MIN_SIMILARITY if settings.EMBEDDER == "gemini" else 0.0
    return QdrantKnowledgeStore(
        client, settings.QDRANT_COLLECTION, settings.EMBEDDING_DIMENSIONS, floor
    )


def build_photo_index(settings: Settings, client: AsyncQdrantClient) -> PhotoIndex:
    return QdrantPhotoIndex(client, settings.QDRANT_PHOTO_COLLECTION, settings.EMBEDDING_DIMENSIONS)


def build_media(settings: Settings) -> MediaStore:
    """Cloudflare R2 when a bucket is set, else this server's disk."""
    if not settings.R2_BUCKET:
        return DiskMedia(settings.MEDIA_DIR)
    return R2Media(
        settings.R2_ACCOUNT_ID,
        settings.R2_ACCESS_KEY_ID,
        settings.R2_SECRET_ACCESS_KEY,
        settings.R2_BUCKET,
        settings.R2_PUBLIC_URL,
    )


def build_photos(
    settings: Settings,
    embedder: Embedder,
    index: PhotoIndex,
    fetch: FetchImage | None = None,
) -> PhotoService:
    """Photo links are fetched from public addresses only; in development localhost too, for
    the test shop in dev/woo-shop."""
    dev = settings.APP_ENV == "development"
    return PhotoService(
        embedder,
        index,
        build_media(settings),
        settings.PHOTO_MIN_SIMILARITY,
        fetch or (lambda url: fetch_image(url, local=dev)),
    )


def build_payments(settings: Settings) -> Gateways:
    """Both gateways; each business's own account comes with every call."""
    if settings.PAYMENTS == "sslcommerz":
        return {"sslcommerz": SslCommerz(), "bkash": BkashCheckout()}
    # no network; no order is paid unless a test says so
    return {"sslcommerz": FakePayments(), "bkash": FakeBkash()}


def build_mailer(settings: Settings) -> Mailer:
    if settings.BREVO_API_KEY:
        return Brevo(settings.BREVO_API_KEY, _required(settings, "MAIL_FROM", "BREVO_API_KEY"))
    return ConsoleMailer()  # development and tests: the emails are printed, not sent


def build_transcriber(settings: Settings) -> Transcriber | None:
    """Speech in words for voice notes, and for calls on Chorus: STT_PROVIDER's service in
    VOICE_LANGUAGE, the fake offline, or none without a Deepgram key (a voice note gets
    "please type")."""
    if settings.LLM_PROVIDER == "fake":
        return FakeTranscriber()
    if settings.STT_PROVIDER == "chorus":
        return ChorusTranscriber(
            settings.GIGALOGY_PROJECT_KEY, settings.GIGALOGY_API_KEY, settings.VOICE_LANGUAGE
        )
    if settings.DEEPGRAM_API_KEY:
        return DeepgramTranscriber(settings.DEEPGRAM_API_KEY, settings.VOICE_LANGUAGE)
    return None


def build_services(
    sessions: async_sessionmaker[AsyncSession],
    llm: LanguageModel,
    embedder: Embedder,
    store: KnowledgeStore,
    photo_index: PhotoIndex,
    settings: Settings,
    now: Callable[[], datetime],
    telegram: TelegramApi | None = None,
    messenger: MessengerApi | None = None,
    whatsapp: WhatsAppApi | None = None,
    payments: Gateways | None = None,
    fetch_sheet: FetchCsv = fetch_csv,
    woo: WooApi | None = None,
    fetch_photo: FetchImage | None = None,
    couriers: dict[str, CourierApi] | None = None,
    redis: Redis | None = None,
) -> Services:
    live = LiveHub(redis)
    watch(sessions, live)
    gateways = payments or build_payments(settings)
    knowledge = KnowledgeService(sessions, embedder, store, now)
    photos = build_photos(settings, embedder, photo_index, fetch_photo)
    agents = AgentService(sessions, knowledge, photos, now)
    telegram = telegram or TelegramBot()
    messenger = messenger or MessengerGraph()
    whatsapp = whatsapp or WhatsAppCloud()
    team = TeamService(sessions, llm, telegram, messenger, whatsapp, now)
    rate_limits = RateLimiter(redis)
    visitors = VisitorService(sessions, settings, now, rate_limits)
    public = settings.PUBLIC_URL.rstrip("/")
    woo_service = WooService(
        sessions,
        agents,
        team,
        woo or WooRest(),
        local_http=settings.APP_ENV == "development",
        hook_base=f"{public}/api/woo/hook" if public.startswith("https://") else "",
    )
    calls = CallService(sessions, agents, team, llm, settings, now)
    inbox = InboxService(sessions, llm, team, now)
    conversations = ConversationService(
        sessions,
        llm,
        knowledge,
        photos,
        team,
        inbox,
        visitors,
        woo_service,
        calls,
        settings,
        now,
    )
    transcriber = build_transcriber(settings)  # one for voice notes and calls
    users = UserService(
        sessions, settings.jwt_secret, build_mailer(settings), now, f"{settings.public_url}/sign-in"
    )
    return Services(
        admin=AdminService(sessions, now),
        agents=agents,
        billing=BillingService(sessions, gateways, settings, now),
        calls=calls,
        channel_links=ChannelLinkService(
            sessions, agents, telegram, messenger, whatsapp, settings, now
        ),
        channels=ChannelService(
            sessions,
            agents,
            conversations,
            visitors,
            photos,
            team,
            telegram,
            messenger,
            whatsapp,
            settings,
            now,
            transcriber,
        ),
        conversations=conversations,
        courier=CourierService(
            sessions,
            agents,
            team,
            couriers or {"steadfast": Steadfast(), "pathao": Pathao(), "fake": FakeCourier(now)},
            # a shop with no courier linked: the fake one, except in production (by hand)
            fallback=None if settings.APP_ENV == "production" else "fake",
            review_after=timedelta(hours=settings.REVIEW_AFTER_HOURS),
        ),
        inbox=inbox,
        knowledge=knowledge,
        live=live,
        members=MemberService(sessions, users, now),
        photos=photos,
        payments=PaymentService(sessions, gateways, team, woo_service, settings, now),
        users=users,
        visitors=visitors,
        rate_limits=rate_limits,
        sheets=SheetService(sessions, agents, team, fetch_sheet),
        team=team,
        transcriber=transcriber,
        woo=woo_service,
    )
