from typing import Literal, Self

from pydantic import field_validator, model_validator
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

    LLM_PROVIDER: Literal["gigalogy", "groq", "fake"] = "gigalogy"
    GROQ_API_KEY: str = ""
    GIGALOGY_PROJECT_KEY: str = ""
    GIGALOGY_API_KEY: str = ""
    GIGALOGY_PROFILE_ID: str = ""  # the Maira profile picks the model, not MODEL
    MODEL: str = "openai/gpt-oss-120b"  # Groq's model for every agent

    # The knowledge base: documents are embedded by Gemini and searched in Qdrant
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

    # Photos: a shop's product photos and the ones visitors send, kept as JPEG files and
    # embedded by the same Gemini model into their own Qdrant collection. On this server's disk
    # in MEDIA_DIR, or in a Cloudflare R2 bucket when R2_BUCKET is set. The bucket is
    # public at R2_PUBLIC_URL: its r2.dev address, or a domain connected to it
    MEDIA_DIR: str = "./media"
    R2_ACCOUNT_ID: str = ""
    R2_ACCESS_KEY_ID: str = ""
    R2_SECRET_ACCESS_KEY: str = ""
    R2_BUCKET: str = ""
    R2_PUBLIC_URL: str = ""
    QDRANT_PHOTO_COLLECTION: str = "heydesk_photos"
    # A product photo below this isn't like the visitor's photo at all. From a run on 13 Oct (14
    # real products): the right product scored 0.84 to 0.95, a look-alike of a missing product up
    # to 0.86, a different kind of thing (a phone for a graphics card) 0.66 to 0.76. No score
    # tells "this one" from "one like it", so the agent always asks; this only drops the unlike.
    PHOTO_MIN_SIMILARITY: float = 0.75

    # Speech-to-text for voice calls: Deepgram streams the words as they're spoken; Chorus and our
    # Whisper (tts_server/kaggle_stt.ipynb on Kaggle) take each utterance
    STT_PROVIDER: Literal["deepgram", "chorus", "whisper"] = "deepgram"
    DEEPGRAM_API_KEY: str = ""
    WHISPER_STT_URL: str = "http://localhost:8011"
    # What every voice call hears and speaks: `bn` is Deepgram in Bangla and the Bangla
    # voice at BANGLA_TTS_URL (tts_server/ or the Kaggle notebook); `en` is Piper
    VOICE_LANGUAGE: Literal["en", "bn"] = "bn"
    BANGLA_TTS_URL: str = "http://localhost:8010"

    # Voice from far away: Cloudflare's free TURN relay carries the audio (dash.cloudflare.com ->
    # Realtime -> TURN). Empty: no relay, which only works on the same network.
    CLOUDFLARE_TURN_KEY_ID: str = ""
    CLOUDFLARE_TURN_API_TOKEN: str = ""
    MAX_CALL_SECONDS: int = 300  # a call hangs up after this long
    # Calls to confirm shop orders: the queue is checked this often; a ring not answered
    # this long is missed; at most this many calls ring or talk at once, on the whole server
    CALL_QUEUE_SECONDS: int = 5
    SNOOZE_CHECK_SECONDS: int = 60  # how often snoozed chats that are due come back
    RING_SECONDS: int = 30
    MAX_LIVE_CALLS: int = 1

    DEV_AGENT_KIND: str = "restaurant"  # its desk: restaurant, clinic, receptionist or shop
    DEV_AGENT_SEED: str = "examples/agents/restaurant.yaml"

    MAX_MESSAGE_CHARS: int = 2000
    MAX_TURNS: int = 60  # visitor messages per conversation; each turn resends the transcript
    CONVERSATION_IDLE_HOURS: int = 12  # on a messaging app, a message after this starts afresh
    TAKEOVER_QUIET_MINUTES: int = 30  # a chat the team took over is the agent's again

    # Limits on a public chat, so timepass can't spend the business's turns. A day is the
    # business's local day.
    VISITOR_DAILY_TURNS: int = 30  # one visitor's turns a day, on any channel
    WORKSPACE_DAILY_TURNS: int = 500  # all of a business's visitors together
    OFF_TOPIC_STRIKES: int = 5  # off-topic turns in a row before a visitor is blocked
    BLOCK_HOURS: int = 24
    FLOOD_MESSAGES: int = 20  # messages in a minute on a messaging app that block a visitor

    SHEET_SYNC_MINUTES: float = 5  # how often a linked Google Sheet is read again
    # how often a linked WooCommerce store is synced when it has no webhooks
    WOO_SYNC_MINUTES: float = 2
    # how often confirmed orders are booked with the courier and parcels followed
    COURIER_SYNC_MINUTES: float = 1
    # a day after delivery the buyer is asked how it was; less to see it in development
    REVIEW_AFTER_HOURS: float = 24

    # Where Telegram can reach this server, https://… (a Cloudflare tunnel to :8000 in
    # development). Needed to connect a business's Telegram bot
    PUBLIC_URL: str = ""

    # Facebook Messenger and WhatsApp: our Meta app. Its webhooks are set once in the
    # app's dashboard to PUBLIC_URL/api/messenger and /api/whatsapp with this verify token; Meta
    # signs each post with the secret
    META_APP_SECRET: str = ""
    # The app's id, for the owner's "Connect with Facebook" button: App settings > Basic
    META_APP_ID: str = ""
    # Facebook Login for Business > Configurations: the one listing the Pages permissions
    # (pages_show_list, pages_messaging, pages_manage_metadata, business_management)
    META_LOGIN_CONFIG_ID: str = ""
    # The WhatsApp Embedded Signup configuration, for "Connect with WhatsApp": the same
    # page, of the WhatsApp Embedded Signup kind (whatsapp_business_management and _messaging)
    META_WA_CONFIG_ID: str = ""
    MESSENGER_VERIFY_TOKEN: str = ""
    # On Telegram, Messenger and WhatsApp the agent waits this long after a visitor's message
    # for another, then answers all of them at once
    CHAT_WAIT_SECONDS: float = 3.0

    # Paying online before an order is placed, into each business's own SSLCommerz store
    # or bKash account, which the owner connects on the frontend. sslcommerz: the
    # real gateways, both of them; fake: tests, no network
    PAYMENTS: Literal["sslcommerz", "fake"] = "sslcommerz"
    # Businesses paying HeyDesk for a plan go to our own SSLCommerz store. Empty: the
    # billing page can't take payments, and an admin sets plans by hand
    BILLING_STORE_ID: str = ""
    BILLING_STORE_PASSWORD: str = ""
    BILLING_SANDBOX: bool = True
    # ...or our own bKash merchant account. Empty: the billing page has no bKash button
    BILLING_BKASH_APP_KEY: str = ""
    BILLING_BKASH_APP_SECRET: str = ""
    BILLING_BKASH_USERNAME: str = ""
    BILLING_BKASH_PASSWORD: str = ""
    BILLING_BKASH_SANDBOX: bool = True

    # Email: the codes that verify an owner's email and reset a password go through Brevo
    # (brevo.com, free for 300 a day) from MAIL_FROM, an address verified there. Without a key,
    # development prints each email in the terminal
    BREVO_API_KEY: str = ""
    MAIL_FROM: str = ""

    LOG_LEVEL: str = "INFO"
    LOG_JSON: bool = True  # False in development for readable lines

    # The commit this server runs, which Render sets; /health shows it, so CI knows when a
    # deploy is live
    RENDER_GIT_COMMIT: str = ""

    @field_validator("DATABASE_URL")
    @classmethod
    def _async_postgres(cls, url: str) -> str:
        """Render gives postgresql://…; SQLAlchemy needs the async driver named in it."""
        if url.startswith("postgresql://"):
            return "postgresql+asyncpg://" + url.removeprefix("postgresql://")
        return url

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
