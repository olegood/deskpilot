"""Who gets in. Every test here is a token the MCP server must refuse, or the one it must not.

Each refusal is a 401 at the transport, before any tool is looked up, and the reason
goes to the log rather than to the caller.
"""

import base64
import json
import time
from collections.abc import AsyncIterator
from typing import Any

import pytest
from joserfc import jwt
from joserfc.jwk import ECKey, OctKey

from paywisp.auth_server.keys import SigningKey
from paywisp.mcp_server.verifier import JWKSTokenVerifier
from tests.conftest import ISSUER, RESOURCE
from tests.mcp_harness import PRM_URL, Stack, running_stack, stack_in_own_task

INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "0"},
    },
}
HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


@pytest.fixture
async def stack() -> AsyncIterator[Stack]:
    async with stack_in_own_task() as running:
        yield running


async def status_with(stack: Stack, token: str | None) -> int:
    async with stack.http(token) as client:
        response = await client.post("/mcp", json=INITIALIZE, headers=HEADERS)
    return response.status_code


def unsigned(claims: dict[str, object]) -> str:
    """An `alg: none` token, built by hand because no careful library will make one."""

    def part(value: dict[str, object]) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()

    return f"{part({'alg': 'none', 'typ': 'at+jwt', 'kid': 'x'})}.{part(claims)}."


# ── the way in ──────────────────────────────────────────────────────────────


async def test_the_agents_token_from_the_token_endpoint_is_accepted(stack: Stack) -> None:
    assert await status_with(stack, await stack.agent_token()) == 200


async def test_no_token_is_401_and_points_at_the_metadata(stack: Stack) -> None:
    """RFC 9728: a client that knows nothing learns where to get a token from here."""
    async with stack.http() as client:
        response = await client.post("/mcp", json=INITIALIZE, headers=HEADERS)

    assert response.status_code == 401
    assert PRM_URL in response.headers["www-authenticate"]


async def test_the_protected_resource_metadata_names_the_issuer(stack: Stack) -> None:
    async with stack.http() as client:
        document = (await client.get(PRM_URL)).json()

    assert document["resource"] == RESOURCE
    # The SDK stores the issuer as a pydantic URL, which gives an empty path a "/".
    # The two name the same server (RFC 3986 §6.2.3), but RFC 8414 compares issuers
    # as strings, so a client doing discovery from here has to know. Deskpilot is
    # configured with the issuer rather than discovering it (D-151).
    assert [url.rstrip("/") for url in document["authorization_servers"]] == [ISSUER]


async def test_health_needs_no_token(stack: Stack) -> None:
    async with stack.http() as client:
        assert (await client.get("/health")).status_code == 200


# ── claims ──────────────────────────────────────────────────────────────────


async def test_a_token_for_another_service_is_refused(stack: Stack) -> None:
    """The audience is what stops a token leaked from one service working at another."""
    assert await status_with(stack, stack.mint(aud="http://some-other-service/api")) == 401


async def test_the_sdk_checks_the_audience_too(monkeypatch: pytest.MonkeyPatch) -> None:
    """Defence in depth that is actually there: with the verifier's own claim checks
    switched off, the SDK still refuses a token for another service, because the
    verifier reports the token's audience rather than this server's address (D-147)."""
    monkeypatch.setattr(JWKSTokenVerifier, "_check_claims", lambda self, claims: None)
    async with stack_in_own_task() as stack:
        assert await status_with(stack, stack.mint(aud="http://some-other-service/api")) == 401
        assert await status_with(stack, stack.mint()) == 200


async def test_a_token_for_two_services_is_refused(stack: Stack) -> None:
    assert await status_with(stack, stack.mint(aud=[RESOURCE, "http://elsewhere/api"])) == 401


async def test_a_token_from_another_issuer_is_refused(stack: Stack) -> None:
    assert await status_with(stack, stack.mint(iss="http://not-paywisp.test")) == 401


async def test_an_expired_token_is_refused(stack: Stack) -> None:
    long_ago = int(time.time()) - 3600
    assert await status_with(stack, stack.mint(iat=long_ago - 300, exp=long_ago)) == 401


async def test_a_token_that_is_not_valid_yet_is_refused(stack: Stack) -> None:
    assert await status_with(stack, stack.mint(nbf=int(time.time()) + 3600)) == 401


@pytest.mark.parametrize("claim", ["nbf", "iat", "exp"])
async def test_a_time_claim_that_is_not_a_number_is_refused(stack: Stack, claim: str) -> None:
    unreadable: dict[str, Any] = {claim: "tomorrow"}
    assert await status_with(stack, stack.mint(**unreadable)) == 401


@pytest.mark.parametrize("missing", ["iss", "aud", "exp", "sub", "client_id"])
async def test_a_token_missing_a_required_claim_is_refused(stack: Stack, missing: str) -> None:
    removed: dict[str, Any] = {missing: None}
    assert await status_with(stack, stack.mint(**removed)) == 401


async def test_a_token_without_read_scope_is_403(stack: Stack) -> None:
    """Valid, but it may not do anything here: every caller needs payments:read."""
    assert await status_with(stack, stack.mint(scope="refunds:write")) == 403


# ── the header and the signature ────────────────────────────────────────────


async def test_a_jwt_that_is_not_an_access_token_is_refused(stack: Stack) -> None:
    """RFC 9068. Same issuer, same key, same claims: still not an access token."""
    assert await status_with(stack, stack.mint(header={"typ": "JWT"})) == 401


async def test_an_unsigned_token_is_refused(stack: Stack) -> None:
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": RESOURCE,
        "sub": "x",
        "client_id": "x",
        "scope": "payments:read refunds:write",
        "exp": now + 300,
    }
    assert await status_with(stack, unsigned(claims)) == 401


@pytest.mark.filterwarnings("ignore:This key should not be used as an oct key")
async def test_the_public_key_used_as_an_hmac_secret_is_refused(stack: Stack) -> None:
    """Algorithm confusion. The public key is public; if the server let the token's
    header choose HS256, anybody could sign with it."""
    public_pem = stack.key.private.as_pem(private=False)
    forged = jwt.encode(
        {"alg": "HS256", "kid": stack.key.kid, "typ": "at+jwt"},
        {
            "iss": ISSUER,
            "aud": RESOURCE,
            "sub": "x",
            "client_id": "x",
            "scope": "payments:read refunds:write",
            "exp": int(time.time()) + 300,
        },
        OctKey.import_key(public_pem),
        algorithms=["HS256"],
    )

    assert await status_with(stack, forged) == 401


async def test_a_token_signed_by_a_stranger_under_the_real_key_id_is_refused(
    stack: Stack,
) -> None:
    stranger = ECKey.generate_key("P-256", private=True)
    impostor = SigningKey(stranger)
    impostor.kid = stack.key.kid

    assert await status_with(stack, stack.mint(key=impostor)) == 401


async def test_a_tampered_token_is_refused(stack: Stack) -> None:
    head, body, signature = stack.mint().split(".")
    claims = json.loads(base64.urlsafe_b64decode(body + "=="))
    claims["scope"] = "payments:read refunds:write"
    widened = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()

    assert await status_with(stack, f"{head}.{widened}.{signature}") == 401


@pytest.mark.parametrize("rubbish", ["", "abc", "a.b", "a.b.c", "Bearer x"])
async def test_rubbish_is_refused(stack: Stack, rubbish: str) -> None:
    assert await status_with(stack, rubbish) == 401


# ── fetching keys ───────────────────────────────────────────────────────────


async def test_keys_are_fetched_once_and_reused(stack: Stack) -> None:
    for _ in range(3):
        assert await status_with(stack, await stack.agent_token()) == 200

    assert stack.auth_transport.jwks_fetches == 1


async def test_made_up_key_ids_cannot_make_it_hammer_the_issuer(stack: Stack) -> None:
    """An unknown key id triggers a refetch, and anybody can invent one."""
    await status_with(stack, await stack.agent_token())
    for index in range(5):
        await status_with(stack, stack.mint(header={"kid": f"made-up-{index}"}))

    assert stack.auth_transport.jwks_fetches == 1


async def test_a_rotated_key_is_picked_up() -> None:
    """The authorization server restarts with a new key. Tokens signed with it work."""
    async with running_stack(jwks_min_refresh_seconds=0) as stack:
        assert await status_with(stack, await stack.agent_token()) == 200

        stack.auth_app.state.key = SigningKey(ECKey.generate_key("P-256", private=True))

        assert await status_with(stack, await stack.agent_token()) == 200
        assert stack.auth_transport.jwks_fetches == 2


async def test_no_keys_means_no_entry() -> None:
    """Fail closed. A server that cannot fetch keys cannot check signatures."""
    async with running_stack() as stack:
        token = await stack.agent_token()
        stack.auth_transport.broken = True

        assert await status_with(stack, token) == 401


async def test_the_key_set_can_be_given_as_an_address() -> None:
    """In a container the issuer's name and the key set's address differ."""
    async with running_stack(jwks_url=f"{ISSUER}/.well-known/jwks.json") as stack:
        assert await status_with(stack, await stack.agent_token()) == 200
        assert "/.well-known/oauth-authorization-server" not in stack.auth_transport.paths


# ── the host header ─────────────────────────────────────────────────────────


async def test_an_unexpected_host_is_refused(stack: Stack) -> None:
    """DNS rebinding: a page on another site pointing a name it controls at this port."""
    async with stack.http(await stack.agent_token(), host="attacker.example") as client:
        response = await client.post("/mcp", json=INITIALIZE, headers=HEADERS)

    assert response.status_code >= 400
