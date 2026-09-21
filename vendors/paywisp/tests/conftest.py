"""Fixtures. The authorization server keeps everything in memory, so it runs in process."""

from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from paywisp.auth_server.app import create_app
from paywisp.auth_server.config import Settings

ISSUER = "http://paywisp-auth.test"
RESOURCE = "http://paywisp-mcp.test/mcp"
AGENT_ID = "deskpilot-agent"
AGENT_SECRET = "an-agent-secret-for-tests"


def auth_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "issuer": ISSUER,
        "resources": [RESOURCE],
        "agent_client_id": AGENT_ID,
        "agent_client_secret": AGENT_SECRET,
    }
    return Settings(_env_file=None, **{**values, **overrides})  # type: ignore[arg-type]


async def running(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """An in-process client with the app's lifespan entered.

    ASGITransport does not run the lifespan on its own, and the lifespan is where
    the signing key and the client registry are built.
    """
    async with (
        AsyncClient(transport=ASGITransport(app=app), base_url=ISSUER) as client,
        app.router.lifespan_context(app),
    ):
        yield client


@pytest.fixture
async def auth_client() -> AsyncIterator[AsyncClient]:
    async for client in running(create_app(auth_settings())):
        yield client
