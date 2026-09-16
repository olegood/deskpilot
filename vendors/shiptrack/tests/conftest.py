"""Fixtures. ShipTrack has no database, so everything runs in process."""

from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from shiptrack.app import create_app
from shiptrack.config import ChaosSettings, Settings

SECRET = "a-shared-secret-for-tests"
KEY_ID = "deskpilot"


def transport(app: FastAPI) -> ASGITransport:
    """A transport that behaves like a real server does on an unhandled error.

    Starlette's 500 handler sends the response and then re-raises, so the process
    running the server logs the traceback. Left at its default the test transport
    propagates that exception instead of returning the 500 a real client would
    see, and every chaos test fails with the chaos exception rather than asserting
    on the response.
    """
    return ASGITransport(app=app, raise_app_exceptions=False)


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None, key_id=KEY_ID, secret=SECRET)  # type: ignore[call-arg]


@pytest.fixture
async def client(settings: Settings) -> AsyncIterator[AsyncClient]:
    app = create_app(settings)
    async with (
        AsyncClient(transport=transport(app), base_url="http://carrier") as client,
        app.router.lifespan_context(app),
    ):
        yield client


@pytest.fixture
async def chaotic(settings: Settings) -> AsyncIterator[AsyncClient]:
    """A carrier that fails every request, for testing what a client does about it."""
    app = create_app(settings.model_copy(update={"chaos": ChaosSettings(error_rate=1.0)}))
    async with (
        AsyncClient(transport=transport(app), base_url="http://carrier") as client,
        app.router.lifespan_context(app),
    ):
        yield client
