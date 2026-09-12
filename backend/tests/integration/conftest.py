"""Fixtures for integration tests that need PostgreSQL.

They use a separate database, <name>_test (deskpilot_test by default), so tests
never touch your development data. Docker Compose creates it on first start.
"""

from collections.abc import AsyncIterator

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncEngine

from deskpilot.config import BACKEND_DIR, DatabaseSettings, Settings
from deskpilot.db.session import create_engine


def alembic_config(database: DatabaseSettings) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.attributes["database_url"] = database.url
    return config


@pytest.fixture(scope="session")
def test_database() -> DatabaseSettings:
    """Settings for the test database, migrated from scratch to the latest revision."""
    database = Settings().database
    database = database.model_copy(update={"name": f"{database.name}_test"})
    assert database.password is not None
    try:
        with psycopg.connect(
            host=database.host,
            port=database.port,
            dbname=database.name,
            user=database.user,
            password=database.password.get_secret_value(),
            connect_timeout=5,
        ):
            pass
    except psycopg.OperationalError as exc:
        pytest.fail(
            f"cannot connect to test database {database.name!r}: {exc}."
            "Is `docker compose up -d` running?"
        )
    config = alembic_config(database)
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    return database


@pytest.fixture
async def engine(test_database: DatabaseSettings) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(test_database)
    yield engine
    await engine.dispose()
