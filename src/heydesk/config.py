from typing import Literal, Self

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_JWT_SECRET = "dev-only-secret-never-used-in-production"  # production refuses an empty one
DEV_SECRETS_KEY = "dev-only-key-never-used-in-production"  # the same for SECRETS_KEY


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    APP_ENV: Literal["development", "test", "production"] = "development"
    DATABASE_URL: str = "sqlite+aiosqlite:///./heydesk.db"
    JWT_SECRET: str = ""  # required in production; development and tests use DEV_JWT_SECRET
    # Encrypts the secrets businesses give us (bot tokens, store passwords) in the database.
    # Required in production. Set it once: with another key the saved ones can't be read
    SECRETS_KEY: str = ""
    CORS_ORIGINS: list[str] = ["http://localhost:5173"]  # Vite dev server

    LLM_PROVIDER: Literal["gigalogy", "groq", "fake"] = "gigalogy"  # D14
    GROQ_API_KEY: str = ""
    GIGALOGY_PROJECT_KEY: str = ""
    GIGALOGY_API_KEY: str = ""
    GIGALOGY_PROFILE_ID: str = ""  # the Maira profile picks the model, not MODEL
    MODEL: str = "openai/gpt-oss-120b"  # Groq's model for every agent

    DECIDER: Literal["jev", "fake"] = "fake"
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

    # Photos (D57): a shop's product photos and the ones visitors send, kept as JPEG files here
    # and embedded by the same Gemini model into their own Qdrant collection
    MEDIA_DIR: str = "./media"
    QDRANT_PHOTO_COLLECTION: str = "heydesk_photos"
    # A product photo below this isn't like the visitor's photo at all. From a run on 13 Oct (14
    # real products): the right product scored 0.84 to 0.95, a look-alike of a missing product up
    # to 0.86, a different kind of thing (a phone for a graphics card) 0.66 to 0.76. No score
    # tells "this one" from "one like it", so the agent always asks; this only drops the unlike.
    PHOTO_MIN_SIMILARITY: float = 0.75

    # Speech-to-text for voice calls: Deepgram streams the words as they're spoken; Chorus and our
    # Whisper (tts_server/kaggle_stt.ipynb on Kaggle, D87) take each utterance (D18, D39)
    STT_PROVIDER: Literal["deepgram", "chorus", "whisper"] = "deepgram"
    DEEPGRAM_API_KEY: str = ""
    WHISPER_STT_URL: str = "http://localhost:8011"
    # What every voice call hears and speaks (D86): `bn` is Deepgram in Bangla and the Bangla
    # voice at BANGLA_TTS_URL (tts_server/, D84, or the Kaggle notebook, D85); `en` is Piper
    VOICE_LANGUAGE: Literal["en", "bn"] = "bn"
    BANGLA_TTS_URL: str = "http://localhost:8010"

    # Voice from far away: Cloudflare's free TURN relay carries the audio (dash.cloudflare.com ->
    # Realtime -> TURN). Empty: no relay, which only works on the same network.
    CLOUDFLARE_TURN_KEY_ID: str = ""
    CLOUDFLARE_TURN_API_TOKEN: str = ""
    MAX_CALL_SECONDS: int = 300  # a call hangs up after this long
    # Calls to confirm shop orders (D89): the queue is checked this often; a ring not answered
    # this long is missed; at most this many calls ring or talk at once, on the whole server
    CALL_QUEUE_SECONDS: int = 5
    RING_SECONDS: int = 30
    MAX_LIVE_CALLS: int = 1

    DEV_AGENT_KIND: str = "restaurant"  # its desk: restaurant, clinic, receptionist or shop
    DEV_AGENT_SEED: str = "examples/agents/restaurant.yaml"

    MAX_MESSAGE_CHARS: int = 2000
    MAX_TURNS: int = 60  # visitor messages per conversation; each turn resends the transcript (D21)
    CONVERSATION_IDLE_HOURS: int = 12  # on a messaging app, a message after this starts afresh

    # Limits on a public chat, so timepass can't spend the business's turns (D51). A day is the
    # business's local day.
    VISITOR_DAILY_TURNS: int = 30  # one visitor's turns a day, on any channel
    WORKSPACE_DAILY_TURNS: int = 500  # all of a business's visitors together
    OFF_TOPIC_STRIKES: int = 5  # off-topic turns in a row before a visitor is blocked
    BLOCK_HOURS: int = 24

    SHEET_SYNC_MINUTES: float = 5  # how often a linked Google Sheet is read again (D58)
    # how often a linked WooCommerce store is synced when it has no webhooks (D75, D78)
    WOO_SYNC_MINUTES: float = 2
    # how often the linked courier is asked about parcels, and what's left is booked (D90)
    COURIER_SYNC_MINUTES: float = 5

    # Where Telegram can reach this server, https://… (a Cloudflare tunnel to :8000 in
    # development). Needed to connect a business's Telegram bot (D42)
    PUBLIC_URL: str = ""

    # Facebook Messenger (D60): our Meta app. Its webhook is set once in the app's dashboard to
    # PUBLIC_URL/api/messenger with this verify token; Facebook signs each post with the secret
    META_APP_SECRET: str = ""
    MESSENGER_VERIFY_TOKEN: str = ""

    # Paying online before an order is placed (D47), into each business's own SSLCommerz store
    # (D49), which the owner connects on the frontend. fake: tests, no network
    PAYMENTS: Literal["sslcommerz", "fake"] = "sslcommerz"

    # Email (D93): the codes that verify an owner's email and reset a password go through Brevo
    # (brevo.com, free for 300 a day) from MAIL_FROM, an address verified there. Without a key,
    # development prints each email in the terminal
    BREVO_API_KEY: str = ""
    MAIL_FROM: str = ""

    LOG_LEVEL: str = "INFO"
    LOG_JSON: bool = True  # False in development for readable lines

    @property
    def jwt_secret(self) -> str:
        return self.JWT_SECRET or DEV_JWT_SECRET

    @property
    def secrets_key(self) -> str:
        return self.SECRETS_KEY or DEV_SECRETS_KEY

    @property
    def public_url(self) -> str:
        """Where a visitor's browser reaches this server: links in replies, payment returns."""
        return (self.PUBLIC_URL or "http://localhost:8000").rstrip("/")

    @model_validator(mode="after")
    def _production_is_real(self) -> Self:
        if self.APP_ENV != "production":
            return self
        problems = []
        if not self.JWT_SECRET:
            problems.append("JWT_SECRET is empty")
        if not self.SECRETS_KEY:
            problems.append("SECRETS_KEY is empty")
        if self.LLM_PROVIDER == "fake":
            problems.append("LLM_PROVIDER is fake")
        if self.DECIDER == "fake":
            problems.append("DECIDER is fake")
        if self.EMBEDDER == "fake":
            problems.append("EMBEDDER is fake")
        if self.PAYMENTS == "fake":
            problems.append("PAYMENTS is fake")
        if not self.BREVO_API_KEY or not self.MAIL_FROM:
            problems.append("BREVO_API_KEY or MAIL_FROM is empty")
        if not self.QDRANT_URL:
            problems.append("QDRANT_URL is empty")
        if problems:
            raise ValueError("refusing to start in production: " + "; ".join(problems))
        return self
