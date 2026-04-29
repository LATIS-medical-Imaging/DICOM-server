"""Async SQLAlchemy engine and session factory.

FastAPI uses the async engine (asyncpg, via PgBouncer).
Celery workers use the sync engine (psycopg2, direct to postgres).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings


def _build_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        settings.database_url_async,
        echo=settings.app_debug,
        pool_pre_ping=True,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout,
    )


_settings = get_settings()
engine: AsyncEngine = _build_engine(_settings)

SessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    autoflush=False,
    class_=AsyncSession,
)


async def get_db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def dispose_engine() -> None:
    await engine.dispose()


# Sync engine — Celery workers only

_sync_engine: Engine | None = None
_SyncSessionLocal: sessionmaker[Session] | None = None


def _get_sync_session_factory() -> sessionmaker[Session]:
    global _sync_engine, _SyncSessionLocal
    if _SyncSessionLocal is None:
        settings: Settings = get_settings()
        _sync_engine = create_engine(
            settings.database_url_sync,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=2,
        )
        _SyncSessionLocal = sessionmaker(
            bind=_sync_engine,
            expire_on_commit=False,
            autoflush=False,
        )
    return _SyncSessionLocal


@contextmanager
def get_sync_db() -> Iterator[Session]:
    factory = _get_sync_session_factory()
    session: Session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
