import pytest
from pydantic import ValidationError

from heydesk.config import Settings


def test_production_refuses_fakes_and_an_empty_secret() -> None:
    with pytest.raises(ValidationError, match="JWT_SECRET is empty.*LLM_PROVIDER is fake"):
        Settings(APP_ENV="production", LLM_PROVIDER="fake", _env_file=None)  # type: ignore[call-arg]


def test_production_starts_when_configured() -> None:
    Settings(
        APP_ENV="production", JWT_SECRET="x", LLM_PROVIDER="groq", DECIDER="jev", _env_file=None
    )  # type: ignore[call-arg]
