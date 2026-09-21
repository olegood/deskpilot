"""The authorization server's HTTP endpoints.

Three of them in this step:

- ``/.well-known/oauth-authorization-server``, the RFC 8414 metadata document
- ``/.well-known/jwks.json``, the public keys
- ``/oauth/token``, which only knows the client credentials grant so far

Errors follow RFC 6749 section 5.2: a JSON body with an `error` code, 400 for most
things and 401 when the client itself could not be authenticated.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from paywisp.auth_server.clients import (
    GRANT_CLIENT_CREDENTIALS,
    Client,
    parse_basic_auth,
    registered_clients,
)
from paywisp.auth_server.config import ALL_SCOPES, Settings
from paywisp.auth_server.keys import ALGORITHM, SigningKey
from paywisp.auth_server.tokens import issue_access_token

logger = logging.getLogger(__name__)

METADATA_PATH = "/.well-known/oauth-authorization-server"
JWKS_PATH = "/.well-known/jwks.json"
TOKEN_PATH = "/oauth/token"  # noqa: S105 - a URL path, not a credential

# RFC 6749 section 5.1: token responses must not be cached by anything in between.
NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


class OAuthError(Exception):
    """An error the token endpoint reports in the RFC 6749 format."""

    def __init__(self, error: str, description: str, status_code: int = 400) -> None:
        super().__init__(description)
        self.error = error
        self.description = description
        self.status_code = status_code


def metadata(settings: Settings) -> dict[str, Any]:
    """What a client needs to know to talk to this server.

    Only what is actually supported is advertised. A client reads this to decide
    what to do, so listing a grant or a method that is not implemented is a way of
    steering it into a failure.
    """
    return {
        "issuer": settings.issuer,
        "token_endpoint": f"{settings.issuer}{TOKEN_PATH}",
        "jwks_uri": f"{settings.issuer}{JWKS_PATH}",
        "grant_types_supported": [GRANT_CLIENT_CREDENTIALS],
        # Required by RFC 8414, and honestly empty: there is no authorization
        # endpoint yet. The authorization code flow arrives with delegated access.
        "response_types_supported": [],
        "token_endpoint_auth_methods_supported": ["client_secret_basic"],
        "scopes_supported": list(ALL_SCOPES),
        # Not in the RFC 8414 registry for this purpose, but the one hint a client
        # can use to check the algorithm it will be handed before it is handed it.
        "access_token_signing_alg_values_supported": [ALGORITHM],
    }


def authenticate_client(request: Request, clients: dict[str, Client]) -> Client:
    """Who is asking. Every failure reads the same.

    Unknown client and wrong secret are one error, so the endpoint cannot be used
    to discover which client ids exist.
    """
    credentials = parse_basic_auth(request.headers.get("authorization"))
    if credentials is None:
        raise OAuthError("invalid_client", "Client authentication failed.", 401)
    client_id, secret = credentials
    client = clients.get(client_id)
    if client is None or not client.verify_secret(secret):
        logger.info("rejected client credentials for %r", client_id)
        raise OAuthError("invalid_client", "Client authentication failed.", 401)
    return client


def requested_scopes(raw: str | None, client: Client) -> frozenset[str]:
    """The scopes to grant, or an error.

    A request must name its scopes. RFC 6749 allows a server to fill in a default,
    but a default grows silently whenever a client's allowance does, and an explicit
    request is the one that can be read in a log and argued with.

    A scope beyond the client's allowance is refused rather than trimmed. Trimming
    is allowed too, and it hides a misconfiguration until the moment the missing
    scope is needed.
    """
    if not raw or not raw.strip():
        raise OAuthError("invalid_scope", "A scope must be requested.")
    scopes = frozenset(raw.split())
    unknown = scopes - set(ALL_SCOPES)
    if unknown:
        raise OAuthError("invalid_scope", f"Unknown scope: {' '.join(sorted(unknown))}.")
    beyond = scopes - client.allowed_scopes
    if beyond:
        logger.warning(
            "%s asked for %s, which it may not have", client.client_id, " ".join(sorted(beyond))
        )
        raise OAuthError("invalid_scope", "The requested scope is not allowed for this client.")
    return scopes


def requested_resource(form_values: list[str], settings: Settings) -> str:
    """The one service the token will be good for (RFC 8707).

    Required. A token without an audience is a token every service trusting this
    issuer would accept, which turns a token leaked from one of them into a key to
    all of them. MCP's authorization spec requires clients to send it; this server
    requires it too, so a client that forgets finds out at once.
    """
    if not form_values:
        raise OAuthError("invalid_target", "A resource must be named.")
    if len(form_values) > 1:
        # RFC 8707 allows several, producing a token for all of them. That is the
        # thing an audience exists to prevent, so it is not supported.
        raise OAuthError("invalid_target", "Name exactly one resource.")
    resource = form_values[0]
    if resource not in settings.resources:
        raise OAuthError("invalid_target", "Tokens are not issued for that resource.")
    return resource


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = settings
        app.state.key = (
            SigningKey.from_pem(settings.signing_key.get_secret_value())
            if settings.signing_key is not None
            else SigningKey.generate()
        )
        app.state.clients = registered_clients(settings)
        logger.info(
            "paywisp auth server ready as %s with %d client(s)",
            settings.issuer,
            len(app.state.clients),
        )
        yield

    app = FastAPI(title="Paywisp authorization server", lifespan=lifespan)

    @app.exception_handler(OAuthError)
    async def _oauth_error(request: Request, exc: OAuthError) -> JSONResponse:
        headers = dict(NO_STORE)
        if exc.status_code == status.HTTP_401_UNAUTHORIZED:
            # RFC 6749 section 5.2 requires this on a 401 from the token endpoint.
            headers["WWW-Authenticate"] = 'Basic realm="paywisp"'
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.error, "error_description": exc.description},
            headers=headers,
        )

    @app.get(METADATA_PATH)
    async def discovery(request: Request) -> dict[str, Any]:
        return metadata(request.app.state.settings)

    @app.get(JWKS_PATH)
    async def jwks(request: Request) -> dict[str, Any]:
        key: SigningKey = request.app.state.key
        return key.public_jwks()

    @app.post(TOKEN_PATH)
    async def token(request: Request) -> JSONResponse:
        # Parsed by hand rather than declared as form fields. A missing field should
        # be an OAuth error a client knows how to read, not FastAPI's 422.
        if not request.headers.get("content-type", "").startswith(
            "application/x-www-form-urlencoded"
        ):
            raise OAuthError(
                "invalid_request", "The body must be application/x-www-form-urlencoded."
            )
        form = await request.form()
        server_settings: Settings = request.app.state.settings

        # The client is authenticated before the grant type is even looked at, so
        # an unauthenticated caller learns nothing about what this server supports
        # beyond what the public metadata already says.
        client = authenticate_client(request, request.app.state.clients)

        grant_type = form.get("grant_type")
        if grant_type != GRANT_CLIENT_CREDENTIALS:
            raise OAuthError("unsupported_grant_type", "That grant type is not supported.")
        if grant_type not in client.grant_types:
            raise OAuthError("unauthorized_client", "This client may not use that grant.")

        scope_value = form.get("scope")
        scopes = requested_scopes(scope_value if isinstance(scope_value, str) else None, client)
        resources = [value for value in form.getlist("resource") if isinstance(value, str)]
        audience = requested_resource(resources, server_settings)

        issued = issue_access_token(
            request.app.state.key,
            issuer=server_settings.issuer,
            # For a service account the subject is the client itself. There is no
            # person behind this token, and the claims say so.
            subject=client.client_id,
            client_id=client.client_id,
            audience=audience,
            scopes=scopes,
            lifetime_seconds=server_settings.access_token_seconds,
        )
        logger.info("issued %s to %s for %s", issued.scope, client.client_id, audience)
        # No refresh token for client credentials. The client can simply ask again,
        # and a long-lived credential that adds nothing is one more thing to leak.
        return JSONResponse(
            content={
                "access_token": issued.access_token,
                "token_type": "Bearer",
                "expires_in": issued.expires_in,
                "scope": issued.scope,
            },
            headers=NO_STORE,
        )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
