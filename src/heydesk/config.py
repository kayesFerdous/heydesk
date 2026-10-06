from typing import Literal, Self

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_JWT_SECRET = "dev-only-secret-never-used-in-production"  # production refuses an empty one


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    APP_ENV: Literal["development", "test", "production"] = "development"
    DATABASE_URL: str = "sqlite+aiosqlite:///./heydesk.db"
    JWT_SECRET: str = ""  # required in production; development and tests use DEV_JWT_SECRET
    CORS_ORIGINS: list[str] = ["http://localhost:5173"]  # Vite dev server

    LLM_PROVIDER: Literal["gigalogy", "groq", "fake"] = "gigalogy"  # D14
    GROQ_API_KEY: str = ""
    GIGALOGY_PROJECT_KEY: str = ""
    GIGALOGY_API_KEY: str = ""
    GIGALOGY_PROFILE_ID: str = ""  # the Maira profile picks the model, not DEFAULT_MODEL
    MODEL: str = "openai/gpt-oss-120b"  # Groq's model for every agent

    DECIDER: Literal["jev", "llm", "fake"] = "fake"
    TYPESAFE_API_KEY: str = ""
    TYPESAFE_MODEL: str = "jev-latest"  # pin a version the day a judgment changes under you
    TYPESAFE_TIMEOUT: float = 5.0

    # The knowledge base (D33, D34): documents are embedded by Gemini and searched in Qdrant
    EMBEDDER: Literal["gemini", "fake"] = "gemini"
    GEMINI_API_KEY: str = ""
    EMBEDDING_MODEL: str = "gemini-embedding-2"
    EMBEDDING_DIMENSIONS: int = 768
    QDRANT_URL: str = ""  # empty: Qdrant's local mode in QDRANT_LOCAL_PATH, for development
    QDRANT_API_KEY: str = ""
    QDRANT_LOCAL_PATH: str = "./qdrant_local"
    QDRANT_COLLECTION: str = "heydesk_chunks"
    # A passage whose meaning-score is below this isn't similar at all and is left out. From a live
    # run on 4 Oct (gemini-embedding-2, 768 dims, English and Bengali): questions the documents
    # answered scored 0.69 to 0.79, ones they didn't 0.50 to 0.60. Raise it to say "not sure" more
    # often, lower it if answerable questions get nothing. Exact-word matches ignore it.
    KNOWLEDGE_MIN_SIMILARITY: float = 0.6

    # Voice from far away: Cloudflare's free TURN relay carries the audio (dash.cloudflare.com ->
    # Realtime -> TURN). Empty: no relay, which only works on the same network.
    CLOUDFLARE_TURN_KEY_ID: str = ""
    CLOUDFLARE_TURN_API_TOKEN: str = ""
    MAX_CALL_SECONDS: int = 300  # a call hangs up after this long

    DEV_AGENT_PRESET: str = "restaurant"
    DEV_AGENT_SEED: str = "examples/agents/restaurant.yaml"

    MAX_MESSAGE_CHARS: int = 2000
    MAX_TURNS: int = 60  # visitor messages per conversation; each turn resends the transcript (D21)
    MAX_ITEMS_IN_PROMPT: int = 60  # more items than this: the model finds them with search_items

    LOG_LEVEL: str = "INFO"
    LOG_JSON: bool = True  # False in development for readable lines

    @property
    def jwt_secret(self) -> str:
        return self.JWT_SECRET or DEV_JWT_SECRET

    @model_validator(mode="after")
    def _production_is_real(self) -> Self:
        if self.APP_ENV != "production":
            return self
        problems = []
        if not self.JWT_SECRET:
            problems.append("JWT_SECRET is empty")
        if self.LLM_PROVIDER == "fake":
            problems.append("LLM_PROVIDER is fake")
        if self.DECIDER == "fake":
            problems.append("DECIDER is fake")
        if self.EMBEDDER == "fake":
            problems.append("EMBEDDER is fake")
        if not self.QDRANT_URL:
            problems.append("QDRANT_URL is empty")
        if problems:
            raise ValueError("refusing to start in production: " + "; ".join(problems))
        return self
