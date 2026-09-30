from datetime import UTC, datetime
from typing import Any, ClassVar

from sqlalchemy import DateTime, Dialect
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import StaticPool
from sqlalchemy.types import TypeDecorator


def utcnow() -> datetime:
    return datetime.now(UTC)


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


class Base(DeclarativeBase):
    type_annotation_map: ClassVar[dict[Any, Any]] = {datetime: UTCDateTime}


def make_engine(url: str) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    kwargs: dict[str, Any] = {}
    if url in ("sqlite+aiosqlite://", "sqlite+aiosqlite:///:memory:"):
        # one shared connection, or every session would see its own empty database
        kwargs = {"poolclass": StaticPool, "connect_args": {"check_same_thread": False}}
    engine = create_async_engine(url, **kwargs)
    return engine, async_sessionmaker(engine, expire_on_commit=False)
