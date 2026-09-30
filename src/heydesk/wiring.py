"""Builds adapters from Settings and hands them to services as ports.

The only module that imports `heydesk.adapters`.
"""

from heydesk.adapters.decider.fake import FakeDecider
from heydesk.adapters.llm.fake import FakeLLM
from heydesk.config import Settings
from heydesk.ports.decider import Decider
from heydesk.ports.llm import LanguageModel
from heydesk.runtime.loop import Runtime
from heydesk.services import Services
from heydesk.services.conversations import ConversationService


def build_llm(settings: Settings) -> LanguageModel:
    if settings.LLM_PROVIDER == "fake":
        return FakeLLM(delay=0.04)
    raise NotImplementedError("GroqLLM arrives in M1 day 4; set LLM_PROVIDER=fake")


def build_decider(settings: Settings) -> Decider:
    if settings.DECIDER == "fake":
        return FakeDecider()
    raise NotImplementedError("JevDecider arrives in M2 day 8; set DECIDER=fake")


def build_services(llm: LanguageModel, decider: Decider, settings: Settings) -> Services:
    runtime = Runtime(llm=llm, decider=decider)
    return Services(conversations=ConversationService(runtime, settings))
