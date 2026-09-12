"""Async engine and session factory."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from deskpilot.config import DatabaseSettings


def create_engine(database: DatabaseSettings) -> AsyncEngine:
    return create_async_engine(
        database.url,
        pool_size=database.pool_size,
        pool_pre_ping=True,
        echo=database.echo_sql,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # expire_on_commit=False: objects stay usable after commit without a new query,
    # which async code can't do implicitly.
    return async_sessionmaker(engine, expire_on_commit=False)
