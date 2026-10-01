"""Builds adapters from Settings and hands them to services as ports.

The only module that imports `heydesk.adapters`.
"""

from collections.abc import Callable
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from heydesk.adapters.decider.fake import FakeDecider
from heydesk.adapters.llm.fake import FakeLLM
from heydesk.adapters.llm.gigalogy import GigalogyLLM
from heydesk.adapters.llm.groq import GroqLLM
from heydesk.config import Settings
from heydesk.ports.decider import Decider
from heydesk.ports.llm import LanguageModel
from heydesk.presets import load_presets
from heydesk.runtime.loop import Runtime
from heydesk.services import Services
from heydesk.services.agents import AgentService
from heydesk.services.conversations import ConversationService
from heydesk.services.items import ItemService


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


def _required(settings: Settings, name: str) -> str:
    value: str = getattr(settings, name)
    if not value:
        raise RuntimeError(f"LLM_PROVIDER={settings.LLM_PROVIDER} needs {name} in .env")
    return value


def build_decider(settings: Settings) -> Decider:
    if settings.DECIDER == "fake":
        return FakeDecider()
    raise NotImplementedError("JevDecider arrives on day 7; set DECIDER=fake")


def build_services(
    sessions: async_sessionmaker[AsyncSession],
    llm: LanguageModel,
    decider: Decider,
    settings: Settings,
    now: Callable[[], datetime],
) -> Services:
    runtime = Runtime(llm=llm, decider=decider)
    items = ItemService(sessions)
    return Services(
        agents=AgentService(sessions, load_presets(), items, settings.MAX_ITEMS_IN_PROMPT, now),
        conversations=ConversationService(sessions, runtime, items, settings, now),
        items=items,
    )
