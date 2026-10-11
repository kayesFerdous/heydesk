"""Builds adapters from Settings and hands them to services as ports.

The only module that imports `heydesk.adapters`.
"""

from collections.abc import Callable
from datetime import datetime

from qdrant_client import AsyncQdrantClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from typesafe_sdk import AsyncTypeSafeClient, RetryPolicy

from heydesk.adapters.decider.fake import FakeDecider
from heydesk.adapters.decider.jev import JevDecider
from heydesk.adapters.embedder.fake import FakeEmbedder
from heydesk.adapters.embedder.gemini import GeminiEmbedder
from heydesk.adapters.knowledge.qdrant import QdrantKnowledgeStore
from heydesk.adapters.llm.fake import FakeLLM
from heydesk.adapters.llm.gigalogy import GigalogyLLM
from heydesk.adapters.llm.groq import GroqLLM
from heydesk.adapters.telegram.bot import TelegramBot
from heydesk.config import Settings
from heydesk.ports.decider import Decider
from heydesk.ports.embedder import Embedder
from heydesk.ports.knowledge_store import KnowledgeStore
from heydesk.ports.llm import LanguageModel
from heydesk.ports.telegram import TelegramApi
from heydesk.runtime.loop import Runtime
from heydesk.services import Services
from heydesk.services.admin import AdminService
from heydesk.services.agents import AgentService
from heydesk.services.channels import ChannelService
from heydesk.services.conversations import ConversationService
from heydesk.services.knowledge import KnowledgeService
from heydesk.services.team import TeamService
from heydesk.services.users import UserService


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


def build_decider(settings: Settings) -> Decider:
    if settings.DECIDER == "jev":
        if not settings.TYPESAFE_API_KEY:
            raise RuntimeError("DECIDER=jev needs TYPESAFE_API_KEY in .env")
        client = AsyncTypeSafeClient(
            api_key=settings.TYPESAFE_API_KEY,
            timeout=settings.TYPESAFE_TIMEOUT,
            retry=RetryPolicy(max_retries=0),  # inside a tool's timeout; a failure fails closed
        )
        return JevDecider(client, settings.TYPESAFE_MODEL)
    return FakeDecider()  # says no to everything: no order is ever confirmed


def build_embedder(settings: Settings) -> Embedder:
    if settings.EMBEDDER == "gemini":
        return GeminiEmbedder(
            _required(settings, "GEMINI_API_KEY", "EMBEDDER=gemini"),
            settings.EMBEDDING_MODEL,
            settings.EMBEDDING_DIMENSIONS,
        )
    return FakeEmbedder(settings.EMBEDDING_DIMENSIONS)  # words only: no meaning, no key


def build_knowledge_store(settings: Settings) -> KnowledgeStore:
    """Qdrant Cloud when `QDRANT_URL` is set; otherwise its local mode, in a folder."""
    if settings.QDRANT_URL:
        client = AsyncQdrantClient(
            url=settings.QDRANT_URL, api_key=settings.QDRANT_API_KEY or None, timeout=20
        )
    else:
        client = AsyncQdrantClient(path=settings.QDRANT_LOCAL_PATH)
    # The floor was measured on Gemini's scores; the fake embedder's mean something else
    floor = settings.KNOWLEDGE_MIN_SIMILARITY if settings.EMBEDDER == "gemini" else 0.0
    return QdrantKnowledgeStore(
        client, settings.QDRANT_COLLECTION, settings.EMBEDDING_DIMENSIONS, floor
    )


def build_services(
    sessions: async_sessionmaker[AsyncSession],
    llm: LanguageModel,
    decider: Decider,
    embedder: Embedder,
    store: KnowledgeStore,
    settings: Settings,
    now: Callable[[], datetime],
    telegram: TelegramApi | None = None,
) -> Services:
    runtime = Runtime(llm=llm, decider=decider)
    knowledge = KnowledgeService(sessions, embedder, store, now)
    agents = AgentService(sessions, knowledge, now)
    telegram = telegram or TelegramBot()
    team = TeamService(sessions, llm, telegram, now)
    conversations = ConversationService(sessions, runtime, knowledge, team, settings, now)
    return Services(
        admin=AdminService(sessions, now),
        agents=agents,
        channels=ChannelService(sessions, agents, conversations, team, telegram, settings, now),
        conversations=conversations,
        knowledge=knowledge,
        users=UserService(sessions, settings.jwt_secret, now),
    )
