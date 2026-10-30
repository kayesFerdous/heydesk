import pytest
from pydantic import ValidationError

from heydesk.config import Settings


def test_production_refuses_fakes_and_empty_secrets() -> None:
    match = "JWT_SECRET is empty; SECRETS_KEY is empty.*LLM_PROVIDER is fake"
    with pytest.raises(ValidationError, match=match):
        Settings(APP_ENV="production", LLM_PROVIDER="fake", _env_file=None)  # type: ignore[call-arg]


def test_renders_postgres_url_gets_the_async_driver() -> None:
    settings = Settings(DATABASE_URL="postgresql://u:p@host/db", _env_file=None)  # type: ignore[call-arg]
    assert settings.DATABASE_URL == "postgresql+asyncpg://u:p@host/db"


def test_production_starts_when_configured() -> None:
    Settings(
        APP_ENV="production",
        JWT_SECRET="x",
        SECRETS_KEY="y",
        LLM_PROVIDER="groq",
        QDRANT_URL="https://example.qdrant.io",
        PAYMENTS="sslcommerz",
        BREVO_API_KEY="xkeysib-test",
        MAIL_FROM="hello@heydesk.test",
        _env_file=None,
    )  # type: ignore[call-arg]
