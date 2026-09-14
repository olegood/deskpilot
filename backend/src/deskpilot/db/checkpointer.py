"""LangGraph checkpointing on PostgreSQL.

Every ticket is one LangGraph thread. Its state - the conversation, the step count,
token usage - is written to the checkpoint tables after each node, so a ticket
survives a process restart and a customer can reply days later.

LangGraph owns these tables and manages their schema itself, so they are created by
`AsyncPostgresSaver.setup()` rather than by an Alembic migration.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import AsyncConnection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool

from deskpilot.config import DatabaseSettings


def psycopg_conninfo(database: DatabaseSettings) -> str:
    """A libpq connection string. psycopg needs this, not a SQLAlchemy URL."""
    # render_as_string(hide_password=False) would give the SQLAlchemy dialect prefix,
    # which psycopg does not understand, so the parts are assembled directly.
    if database.password is None:  # Settings validation normally prevents this.
        raise ValueError("DESKPILOT_DATABASE__PASSWORD is not set")
    parts = {
        "host": database.host,
        "port": str(database.port),
        "dbname": database.name,
        "user": database.user,
        "password": database.password.get_secret_value(),
    }
    return " ".join(f"{key}={value}" for key, value in parts.items())


@asynccontextmanager
async def open_checkpointer(database: DatabaseSettings) -> AsyncIterator[AsyncPostgresSaver]:
    """Open a checkpointer over its own connection pool, and close it afterwards.

    The pool is separate from SQLAlchemy's: LangGraph uses raw psycopg connections
    and manages its own transactions.
    """
    # LangGraph reads rows as dicts, and its queries need autocommit and no
    # server-side prepared statements.
    pool: AsyncConnectionPool[AsyncConnection[DictRow]] = AsyncConnectionPool(
        conninfo=psycopg_conninfo(database),
        max_size=database.pool_size,
        open=False,
        connection_class=AsyncConnection[DictRow],
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    )
    async with pool:
        yield AsyncPostgresSaver(pool)


async def setup_checkpointer(database: DatabaseSettings) -> None:
    """Create or migrate LangGraph's checkpoint tables. Safe to run repeatedly."""
    async with open_checkpointer(database) as checkpointer:
        await checkpointer.setup()
