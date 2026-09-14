"""Fixtures for integration tests, which need real PostgreSQL and real Ollama.

Database tests use a separate database, <name>_test (deskpilot_test by default), so
they never touch your development data. Docker Compose creates it on first start.

Each fixture fails with an actionable message when a service is missing, instead of
letting tests fail with a connection error buried in a stack trace.
"""

import asyncio
from collections.abc import AsyncIterator

import httpx
import psycopg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from deskpilot.config import BACKEND_DIR, DatabaseSettings, Provider, Settings
from deskpilot.db.checkpointer import setup_checkpointer
from deskpilot.db.seed import seed
from deskpilot.db.session import create_engine, create_session_factory


def alembic_config(database: DatabaseSettings) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.attributes["database_url"] = database.url
    return config


@pytest.fixture(scope="session")
def test_database() -> DatabaseSettings:
    """Settings for the test database, migrated from scratch to the latest revision."""
    database = Settings().database
    database = database.model_copy(update={"name": f"{database.name}_test"})
    assert database.password is not None  # guaranteed by Settings validation
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
            f"cannot connect to test database {database.name!r}: {exc}. "
            "Is `docker compose up -d` running?"
        )
    config = alembic_config(database)
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    # LangGraph owns its checkpoint tables and creates them itself; Alembic is told
    # to leave them alone, so they are set up separately here.
    asyncio.run(setup_checkpointer(database))
    return database


@pytest.fixture
async def engine(test_database: DatabaseSettings) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(test_database)
    yield engine
    await engine.dispose()


@pytest.fixture
async def seeded_sessions(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """A session factory over a freshly seeded test database."""
    session_factory = create_session_factory(engine)
    async with session_factory() as session:
        await seed(session, reset=True)
    return session_factory


@pytest.fixture(scope="session")
def agent_settings() -> Settings:
    """Real settings from backend/.env, after checking Ollama has the agent model."""
    settings = Settings()
    if settings.agent.provider is not Provider.OLLAMA:
        pytest.skip("the agent role is not configured for Ollama")
    base_url = str(settings.ollama_base_url).rstrip("/")
    try:
        response = httpx.get(f"{base_url}/api/tags", timeout=5.0)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        pytest.fail(f"Ollama is not reachable at {base_url}: {exc}. Is ollama-serve.sh running?")
    pulled = {model["name"] for model in response.json()["models"]}
    wanted = settings.agent.model
    if wanted not in pulled and f"{wanted}:latest" not in pulled:
        pytest.fail(f"model {wanted!r} is not pulled; run: ollama pull {wanted}")
    return settings
