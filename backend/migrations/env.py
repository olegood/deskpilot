"""Alembic environment for Deskpilot.

The database URL comes from Deskpilot settings (backend/.env), never from
alembic.ini. Tests can pass a different URL through config.attributes.
"""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import URL
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from deskpilot.config import get_settings
from deskpilot.db.models import Base

config = context.config

if config.config_file_name is not None:
    # Keep loggers configured by the called (e.g. pytest) working.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def database_url() -> URL:
    url = config.attributes.get("database_url")
    if isinstance(url, URL):
        return url
    return get_settings().database.url


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of running it (alembic upgrade --sql)."""
    context.configure(
        url=database_url().render_as_string(hide_password=True),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    engine = create_async_engine(database_url(), poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(do_run_migrations)
    finally:
        await engine.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
