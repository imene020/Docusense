"""Database engines.

Accepts any Postgres URL format (including the plain `postgresql://...?sslmode=require`
strings that Neon and other hosts hand out) and derives the async (asyncpg) and
sync (psycopg2) variants from it.
"""
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import get_settings

settings = get_settings()


def _split(url: str):
    parts = urlsplit(url)
    scheme = parts.scheme.split("+")[0]
    return parts, scheme, dict(parse_qsl(parts.query))


def _async_url(url: str) -> tuple[str, dict]:
    parts, scheme, query = _split(url)
    connect_args = {}
    if scheme in ("postgresql", "postgres"):
        # asyncpg does not understand libpq's sslmode / channel_binding params
        sslmode = query.pop("sslmode", None)
        query.pop("channel_binding", None)
        if sslmode and sslmode != "disable":
            connect_args["ssl"] = True
        return urlunsplit(parts._replace(scheme="postgresql+asyncpg", query=urlencode(query))), connect_args
    if scheme == "sqlite":
        return urlunsplit(parts._replace(scheme="sqlite+aiosqlite")), connect_args
    return url, connect_args


def _sync_url(url: str) -> str:
    parts, scheme, _ = _split(url)
    if scheme in ("postgresql", "postgres"):
        return urlunsplit(parts._replace(scheme="postgresql+psycopg2"))
    if scheme == "sqlite":
        return urlunsplit(parts._replace(scheme="sqlite"))
    return url


_async, _connect_args = _async_url(settings.database_url)
_pool_kwargs = {} if _async.startswith("sqlite") else {"pool_size": 5, "max_overflow": 10, "pool_pre_ping": True}
engine = create_async_engine(_async, connect_args=_connect_args, **_pool_kwargs)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

# Sync engine for ingestion (Celery tasks and inline background tasks are not async)
sync_engine = create_engine(_sync_url(settings.sync_database_url or settings.database_url), pool_pre_ping=True)
SyncSessionLocal = sessionmaker(bind=sync_engine)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
