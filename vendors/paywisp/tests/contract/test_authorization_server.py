"""Contract tests for an OAuth authorization server.

These describe what Deskpilot relies on, not how Paywisp happens to be built. Every
endpoint is found through the RFC 8414 metadata document, and nothing here imports
the server under test, so the same file can be pointed at a different one:

    PAYWISP_CONTRACT_ISSUER=https://keycloak.example/realms/paywisp \\
    PAYWISP_CONTRACT_CLIENT_ID=deskpilot-agent \\
    PAYWISP_CONTRACT_CLIENT_SECRET=... \\
    PAYWISP_CONTRACT_RESOURCE=http://127.0.0.1:8210/mcp \\
    uv run pytest tests/contract

That is how replacing Paywisp's server with Keycloak, which is on Deskpilot's
backlog, gets validated. Whether another server passes is exactly what running them
finds out; the tests are written to be fair to one that is correct, not to one that
resembles Paywisp.
"""

from __future__ import annotations

import os
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import pytest
from httpx import AsyncClient, BasicAuth, Response
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import KeySet

from paywisp.auth_server.app import create_app
from tests.conftest import AGENT_ID, AGENT_SECRET, ISSUER, RESOURCE, auth_settings, running

# JWK members that exist only on a private key, for any key type.
PRIVATE_MEMBERS = {"d", "p", "q", "dp", "dq", "qi", "k", "oth"}


@dataclass(frozen=True)
class Contract:
    http: AsyncClient
    issuer: str
    client_id: str
    client_secret: str
    resource: str
    # A scope the client may have, and one that exists but it may not.
    allowed_scope: str
    forbidden_scope: str


@pytest.fixture
async def contract() -> AsyncIterator[Contract]:
    external = os.environ.get("PAYWISP_CONTRACT_ISSUER")
    if external:
        async with AsyncClient(timeout=10) as http:
            yield Contract(
                http=http,
                issuer=external,
                client_id=os.environ["PAYWISP_CONTRACT_CLIENT_ID"],
                client_secret=os.environ["PAYWISP_CONTRACT_CLIENT_SECRET"],
                resource=os.environ["PAYWISP_CONTRACT_RESOURCE"],
                allowed_scope=os.environ.get("PAYWISP_CONTRACT_ALLOWED_SCOPE", "payments:read"),
                forbidden_scope=os.environ.get("PAYWISP_CONTRACT_FORBIDDEN_SCOPE", "refunds:write"),
            )
        return
    async for http in running(create_app(auth_settings())):
        yield Contract(
            http=http,
            issuer=ISSUER,
            client_id=AGENT_ID,
            client_secret=AGENT_SECRET,
            resource=RESOURCE,
            allowed_scope="payments:read",
            forbidden_scope="refunds:write",
        )


async def discover(contract: Contract) -> dict[str, Any]:
    response = await contract.http.get(f"{contract.issuer}/.well-known/oauth-authorization-server")
    assert response.status_code == 200
    document: dict[str, Any] = response.json()
    return document


async def request_token(
    contract: Contract,
    *,
    scope: str | None = None,
    secret: str | None = None,
    client_id: str | None = None,
    grant_type: str = "client_credentials",
    authenticate: bool = True,
) -> Response:
    endpoint = (await discover(contract))["token_endpoint"]
    form = {
        "grant_type": grant_type,
        "scope": scope if scope is not None else contract.allowed_scope,
        "resource": contract.resource,
    }
    auth = (
        BasicAuth(client_id or contract.client_id, secret or contract.client_secret)
        if authenticate
        else None
    )
    return await contract.http.post(endpoint, data=form, auth=auth)


async def verify(contract: Contract, token: str) -> jwt.Token:
    """Check a token the way a careful resource server would."""
    jwks_uri = (await discover(contract))["jwks_uri"]
    keys = KeySet.import_key_set((await contract.http.get(jwks_uri)).json())
    # Asymmetric algorithms only. Accepting HS256 here is how a public key gets
    # used as an HMAC secret to forge tokens.
    return jwt.decode(token, keys, algorithms=["ES256", "RS256", "PS256", "EdDSA"])


# ── discovery ───────────────────────────────────────────────────────────────


async def test_the_issuer_in_the_metadata_is_the_one_asked_for(contract: Contract) -> None:
    """RFC 8414 section 3.3. A mismatch is how a client gets pointed at an impostor."""
    assert (await discover(contract))["issuer"] == contract.issuer


async def test_the_removed_grants_are_not_offered(contract: Contract) -> None:
    """OAuth 2.1 removes implicit and password. Neither should be on the menu."""
    grants = set((await discover(contract)).get("grant_types_supported", []))

    assert "implicit" not in grants
    assert "password" not in grants
    assert "client_credentials" in grants


async def test_the_key_set_holds_no_private_material(contract: Contract) -> None:
    jwks_uri = (await discover(contract))["jwks_uri"]
    keys = (await contract.http.get(jwks_uri)).json()["keys"]

    assert keys
    for key in keys:
        assert not PRIVATE_MEMBERS & set(key), key.get("kid")


# ── the client credentials grant ────────────────────────────────────────────


async def test_a_valid_client_gets_a_bearer_token(contract: Contract) -> None:
    response = await request_token(contract)

    assert response.status_code == 200
    body = response.json()
    assert body["access_token"]
    assert body["token_type"].lower() == "bearer"
    assert body["expires_in"] > 0


async def test_the_response_must_not_be_cached(contract: Contract) -> None:
    """RFC 6749 section 5.1. A cached token is a token in somebody else's store."""
    response = await request_token(contract)

    assert "no-store" in response.headers.get("cache-control", "")


async def test_no_refresh_token_for_a_service_account(contract: Contract) -> None:
    """The client can simply ask again. A longer credential adds only risk."""
    assert "refresh_token" not in (await request_token(contract)).json()


async def test_the_token_verifies_against_the_published_keys(contract: Contract) -> None:
    token = await verify(contract, (await request_token(contract)).json()["access_token"])

    assert token.header["alg"] in {"ES256", "RS256", "PS256", "EdDSA"}


async def test_the_token_is_marked_as_an_access_token(contract: Contract) -> None:
    """RFC 9068. Without it, any other JWT this issuer signs could pass as one."""
    token = await verify(contract, (await request_token(contract)).json()["access_token"])

    assert token.header.get("typ", "").lower() in {"at+jwt", "application/at+jwt"}


async def test_the_token_is_for_the_resource_that_was_named(contract: Contract) -> None:
    """RFC 8707. The audience is what stops a token working at a different service."""
    token = await verify(contract, (await request_token(contract)).json()["access_token"])

    audience = token.claims["aud"]
    assert audience == contract.resource or audience == [contract.resource]


async def test_the_token_names_its_issuer_and_expires_soon(contract: Contract) -> None:
    token = await verify(contract, (await request_token(contract)).json()["access_token"])

    assert token.claims["iss"] == contract.issuer
    assert token.claims["exp"] > time.time()
    assert token.claims["exp"] - token.claims["iat"] <= 3600


async def test_the_token_carries_the_granted_scope(contract: Contract) -> None:
    token = await verify(contract, (await request_token(contract)).json()["access_token"])

    assert contract.allowed_scope in token.claims["scope"].split()


async def test_a_tampered_token_does_not_verify(contract: Contract) -> None:
    token = (await request_token(contract)).json()["access_token"]
    head, body, signature = token.split(".")
    flipped = signature[:-2] + ("AA" if signature[-2:] != "AA" else "BB")

    with pytest.raises(JoseError):
        await verify(contract, f"{head}.{body}.{flipped}")


# ── what is refused ─────────────────────────────────────────────────────────


async def test_a_wrong_secret_is_invalid_client(contract: Contract) -> None:
    response = await request_token(contract, secret="not-the-secret")

    assert response.status_code == 401
    assert response.json()["error"] == "invalid_client"
    assert "www-authenticate" in response.headers


async def test_an_unknown_client_reads_like_a_wrong_secret(contract: Contract) -> None:
    """Otherwise the endpoint tells anybody which client ids exist."""
    wrong_secret = await request_token(contract, secret="not-the-secret")
    unknown = await request_token(contract, client_id="nobody-registered-this")

    assert unknown.status_code == wrong_secret.status_code == 401
    assert unknown.json()["error"] == wrong_secret.json()["error"] == "invalid_client"


async def test_an_unauthenticated_request_is_refused(contract: Contract) -> None:
    response = await request_token(contract, authenticate=False)

    assert response.status_code in {400, 401}
    assert response.json()["error"] in {"invalid_client", "invalid_request"}


async def test_a_scope_the_client_may_not_have_is_refused(contract: Contract) -> None:
    """The property Deskpilot's agent depends on: read-only means it cannot get write."""
    response = await request_token(contract, scope=contract.forbidden_scope)

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_scope"
    assert "access_token" not in response.json()


async def test_the_password_grant_does_not_work(contract: Contract) -> None:
    response = await request_token(contract, grant_type="password")

    assert response.status_code == 400
    assert response.json()["error"] in {"unsupported_grant_type", "unauthorized_client"}


async def test_the_body_must_be_a_form(contract: Contract) -> None:
    endpoint = (await discover(contract))["token_endpoint"]

    response = await contract.http.post(
        endpoint,
        json={"grant_type": "client_credentials", "scope": contract.allowed_scope},
        auth=BasicAuth(contract.client_id, contract.client_secret),
    )

    assert response.status_code in {400, 401, 415}
    assert "access_token" not in response.text
