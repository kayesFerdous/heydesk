import base64
import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any, ClassVar

from cryptography.fernet import Fernet
from sqlalchemy import DateTime, Dialect, MetaData, String
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Session
from sqlalchemy.pool import StaticPool
from sqlalchemy.types import TypeDecorator

from heydesk.config import DEV_SECRETS_KEY


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return uuid.uuid4().hex


class UTCDateTime(TypeDecorator[datetime]):
    """Aware datetimes in, aware UTC datetimes out, on every database.

    SQLite drops the timezone; this stores UTC and puts it back on read.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime: pass an aware one (UTC)")
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def secret_box(key: str) -> Fernet:
    """Fernet (AES with an HMAC) from any long random string, such as `SECRETS_KEY`."""
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(key.encode()).digest()))


_box = secret_box(DEV_SECRETS_KEY)  # development and tests; the app sets the real key


def use_secrets_key(key: str) -> None:
    """The key `Encrypted` columns use. `create_app` sets it from `SECRETS_KEY` at start."""
    global _box
    _box = secret_box(key)


class Encrypted(TypeDecorator[str]):
    """A secret a business gave us (a bot token, a store password): encrypted in the
    database, plain text in Python. Without the key, a copy of the database can't use it.

    The same text encrypts differently each time, so a query can't filter on it."""

    impl = String(1000)  # a 500-character token is about 760 once encrypted
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect: Dialect) -> str | None:
        return None if value is None else _box.encrypt(value.encode()).decode()

    def process_result_value(self, value: str | None, dialect: Dialect) -> str | None:
        return None if value is None else _box.decrypt(value.encode()).decode()


class Base(DeclarativeBase):
    # Named constraints: Alembic's batch mode on SQLite can't drop or alter unnamed ones
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(column_0_label)s",
            "uq": "uq_%(table_name)s_%(column_0_N_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )
    type_annotation_map: ClassVar[dict[Any, Any]] = {datetime: UTCDateTime}


def make_engine(url: str) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    kwargs: dict[str, Any] = {}
    if url in ("sqlite+aiosqlite://", "sqlite+aiosqlite:///:memory:"):
        # one shared connection, or every session would see its own empty database
        kwargs = {"poolclass": StaticPool, "connect_args": {"check_same_thread": False}}
    # JSON keeps Bangla as letters, not \u escapes, so a search can find it
    engine = create_async_engine(url, json_serializer=readable_json, **kwargs)
    # its own session class, so live.watch listens to this database's sessions and no others
    watched = type("WatchedSession", (Session,), {})
    return engine, async_sessionmaker(engine, expire_on_commit=False, sync_session_class=watched)


def readable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)
