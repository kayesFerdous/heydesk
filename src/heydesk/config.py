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

    DEV_AGENT_PRESET: str = "restaurant"
    DEV_AGENT_SEED: str = "examples/agents/restaurant.yaml"

    MAX_MESSAGE_CHARS: int = 2000
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
        if problems:
            raise ValueError("refusing to start in production: " + "; ".join(problems))
        return self
