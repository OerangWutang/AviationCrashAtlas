"""
Async SQLAlchemy engine + session factory.

get_db()       — FastAPI dependency (commits on clean exit, rolls back on error)
get_auth_db()  — FastAPI dependency for auth audit writes committed explicitly
direct_session() — async context manager for CLI / ingestion scripts
"""
from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from atlas.config import get_settings

settings = get_settings()

_engine = create_async_engine(
    settings.database_url,
    pool_size=settings.database_pool_size,
    max_overflow=settings.database_pool_max_overflow,
    echo=settings.api_debug,
)

_SessionFactory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    _engine,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency for write endpoints — yields a session, commits on success."""
    async with _SessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_read_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency for read-only endpoints.
    Never commits — prevents accidental mutations from being silently persisted.
    Rolls back on error so asyncpg connections are not left in a broken state.
    """
    async with _SessionFactory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def get_auth_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency for API-key authentication bookkeeping.

    Auth code explicitly commits the tiny audit write that updates
    api_keys.last_used_at.  This dependency intentionally does not commit on
    clean exit, so a read route cannot accidentally persist unrelated ORM
    mutations through the auth session.  Any uncommitted SELECT-only
    transaction is rolled back when the dependency closes.
    """
    async with _SessionFactory() as session:
        try:
            yield session
            if session.in_transaction():
                await session.rollback()
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def direct_session() -> AsyncGenerator[AsyncSession, None]:
    """Context manager for CLI and ingestion scripts."""
    async with _SessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
