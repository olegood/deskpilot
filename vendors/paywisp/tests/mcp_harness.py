"""Both Paywisp services running in process, and a client for the MCP one.

The MCP server fetches its keys from the real authorization server's JWKS, over an
in-process transport, exactly as it would over the network. Nothing is shortcut:
if the verifier stopped fetching keys or started trusting a shared object, these
tests would stop passing.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx
import httpx2
from fastapi import FastAPI
from joserfc import jwt
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult
from starlette.applications import Starlette

from paywisp.auth_server.app import create_app as create_auth_app
from paywisp.auth_server.keys import SigningKey
from paywisp.mcp_server.config import Settings as McpSettings
from paywisp.mcp_server.payments import PaymentStore, seeded_store
from paywisp.mcp_server.server import create_app as create_mcp_app
from paywisp.mcp_server.verifier import JWKSTokenVerifier
from tests.conftest import AGENT_ID, AGENT_SECRET, ISSUER, RESOURCE, auth_settings

MCP_HOST = "paywisp-mcp.test"
PRM_URL = f"http://{MCP_HOST}/.well-known/oauth-protected-resource/mcp"


class CountingTransport(httpx.AsyncBaseTransport):
    """Wraps a transport and counts requests, so JWKS fetches can be asserted on."""

    def __init__(self, inner: httpx.AsyncBaseTransport) -> None:
        self.inner = inner
        self.paths: list[str] = []
        self.broken = False

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.paths.append(request.url.path)
        if self.broken:
            raise httpx.ConnectError("the authorization server is down")
        return await self.inner.handle_async_request(request)

    @property
    def jwks_fetches(self) -> int:
        return self.paths.count("/.well-known/jwks.json")


@dataclass
class Stack:
    auth_app: FastAPI
    auth_http: httpx.AsyncClient
    auth_transport: CountingTransport
    mcp_app: Starlette
    store: PaymentStore
    settings: McpSettings

    @property
    def key(self) -> SigningKey:
        key: SigningKey = self.auth_app.state.key
        return key

    async def agent_token(self) -> str:
        """A token the way Deskpilot's agent gets one: from the token endpoint."""
        response = await self.auth_http.post(
            "/oauth/token",
            data={
                "grant_type": "client_credentials",
                "scope": "payments:read",
                "resource": RESOURCE,
            },
            auth=(AGENT_ID, AGENT_SECRET),
        )
        token: str = response.json()["access_token"]
        return token

    def mint(
        self,
        *,
        scope: str = "payments:read",
        key: SigningKey | None = None,
        header: dict[str, Any] | None = None,
        **claims: Any,
    ) -> str:
        """A token signed with a real key, with any claim overridden.

        For what the token endpoint would never issue: a write scope for the agent's
        client, or a deliberately wrong audience, issuer, or type.
        """
        signer = key or self.key
        now = int(time.time())
        body = {
            "iss": ISSUER,
            "sub": "reviewer-1",
            "aud": RESOURCE,
            "client_id": "deskpilot-reviewer",
            "scope": scope,
            "iat": now,
            "exp": now + 300,
            "jti": uuid.uuid4().hex,
            **claims,
        }
        head = {"alg": "ES256", "kid": signer.kid, "typ": "at+jwt", **(header or {})}
        return jwt.encode(head, {k: v for k, v in body.items() if v is not None}, signer.private)

    def http(self, token: str | None = None, host: str = MCP_HOST) -> httpx2.AsyncClient:
        headers = {"Host": host}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        return httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=self.mcp_app),
            base_url=f"http://{host}",
            headers=headers,
        )

    @asynccontextmanager
    async def session(self, token: str) -> AsyncIterator[ClientSession]:
        async with (
            self.http(token) as client,
            streamable_http_client(RESOURCE, http_client=client) as streams,
            ClientSession(streams[0], streams[1]) as session,
        ):
            await session.initialize()
            yield session

    async def call(self, token: str, tool: str, **arguments: Any) -> CallToolResult:
        async with self.session(token) as session:
            result = await session.call_tool(tool, arguments)
        assert isinstance(result, CallToolResult)
        return result


@asynccontextmanager
async def running_stack(**mcp_overrides: Any) -> AsyncIterator[Stack]:
    auth_app = create_auth_app(auth_settings())
    async with auth_app.router.lifespan_context(auth_app):
        counting = CountingTransport(httpx.ASGITransport(app=auth_app))
        async with httpx.AsyncClient(transport=counting, base_url=ISSUER) as auth_http:
            settings = McpSettings(
                _env_file=None,
                issuer=ISSUER,
                resource=RESOURCE,
                allowed_hosts=[MCP_HOST],
                **mcp_overrides,
            )
            store = seeded_store()
            verifier = JWKSTokenVerifier(settings, http=auth_http)
            mcp_app = create_mcp_app(settings, verifier, store)
            async with mcp_app.router.lifespan_context(mcp_app):
                yield Stack(auth_app, auth_http, counting, mcp_app, store, settings)


@asynccontextmanager
async def stack_in_own_task(**mcp_overrides: Any) -> AsyncIterator[Stack]:
    """`running_stack` for a pytest fixture.

    The MCP app's lifespan opens an anyio task group, which must be closed by the
    task that opened it. pytest-asyncio may set a fixture up and tear it down in
    different tasks, so the stack lives in a task of its own and is told when to stop.
    """
    ready: asyncio.Future[Stack] = asyncio.get_running_loop().create_future()
    stop = asyncio.Event()

    async def hold() -> None:
        try:
            async with running_stack(**mcp_overrides) as stack:
                ready.set_result(stack)
                await stop.wait()
        except BaseException as exc:
            if not ready.done():
                ready.set_exception(exc)
            raise

    task = asyncio.create_task(hold())
    try:
        yield await ready
    finally:
        stop.set()
        await task
