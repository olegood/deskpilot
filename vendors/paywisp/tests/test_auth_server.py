"""Paywisp's own policies, beyond what the contract requires of any server."""

import base64

import pytest
from httpx import AsyncClient, BasicAuth, Response
from joserfc import jwt
from joserfc.jwk import ECKey, KeySet

from paywisp.auth_server.app import create_app
from paywisp.auth_server.clients import parse_basic_auth
from paywisp.auth_server.keys import SigningKey
from tests.conftest import AGENT_ID, AGENT_SECRET, RESOURCE, auth_settings, running

TOKEN = "/oauth/token"


async def ask(
    client: AsyncClient,
    form: dict[str, str | list[str]] | None = None,
    auth: BasicAuth | None = None,
) -> Response:
    body: dict[str, str | list[str]] = {
        "grant_type": "client_credentials",
        "scope": "payments:read",
        "resource": RESOURCE,
    }
    if form is not None:
        body = {**body, **form}
    return await client.post(TOKEN, data=body, auth=auth or BasicAuth(AGENT_ID, AGENT_SECRET))


# ── scopes ──────────────────────────────────────────────────────────────────


async def test_a_scope_must_be_named(auth_client: AsyncClient) -> None:
    """No default. A default grows silently whenever the client's allowance does."""
    response = await ask(auth_client, {"scope": ""})

    assert response.json()["error"] == "invalid_scope"


async def test_a_request_mixing_allowed_and_forbidden_scopes_is_refused_whole(
    auth_client: AsyncClient,
) -> None:
    """Refused, not trimmed. Trimming hides the misconfiguration until it matters."""
    response = await ask(auth_client, {"scope": "payments:read refunds:write"})

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_scope"


async def test_an_unknown_scope_is_refused(auth_client: AsyncClient) -> None:
    response = await ask(auth_client, {"scope": "payments:everything"})

    assert response.json()["error"] == "invalid_scope"


# ── resources ───────────────────────────────────────────────────────────────


async def test_a_resource_must_be_named(auth_client: AsyncClient) -> None:
    """A token with no audience is a token every service would accept."""
    response = await auth_client.post(
        TOKEN,
        data={"grant_type": "client_credentials", "scope": "payments:read"},
        auth=BasicAuth(AGENT_ID, AGENT_SECRET),
    )

    assert response.json()["error"] == "invalid_target"


async def test_an_unknown_resource_is_refused(auth_client: AsyncClient) -> None:
    response = await ask(auth_client, {"resource": "http://somewhere-else.test/api"})

    assert response.json()["error"] == "invalid_target"


async def test_two_resources_are_refused(auth_client: AsyncClient) -> None:
    """RFC 8707 allows it. A token valid in two places is what audiences prevent."""
    response = await ask(auth_client, {"resource": [RESOURCE, "http://other.test/mcp"]})

    assert response.json()["error"] == "invalid_target"


# ── clients ─────────────────────────────────────────────────────────────────


async def test_the_client_is_checked_before_the_grant(auth_client: AsyncClient) -> None:
    """An unauthenticated caller learns nothing about which grants exist."""
    response = await ask(
        auth_client,
        {"grant_type": "authorization_code"},
        auth=BasicAuth(AGENT_ID, "wrong"),
    )

    assert response.json()["error"] == "invalid_client"


async def test_with_no_secret_configured_the_client_does_not_exist() -> None:
    async for client in running(create_app(auth_settings(agent_client_secret=None))):
        response = await ask(client)

        assert response.status_code == 401


async def test_the_subject_of_a_service_token_is_the_client(auth_client: AsyncClient) -> None:
    """There is no person behind this token, and the claims say so."""
    access = (await ask(auth_client)).json()["access_token"]
    keys = KeySet.import_key_set((await auth_client.get("/.well-known/jwks.json")).json())

    claims = jwt.decode(access, keys, algorithms=["ES256"]).claims

    assert claims["sub"] == AGENT_ID
    assert claims["client_id"] == AGENT_ID


async def test_every_token_has_its_own_id(auth_client: AsyncClient) -> None:
    keys = KeySet.import_key_set((await auth_client.get("/.well-known/jwks.json")).json())
    first = jwt.decode((await ask(auth_client)).json()["access_token"], keys, algorithms=["ES256"])
    second = jwt.decode((await ask(auth_client)).json()["access_token"], keys, algorithms=["ES256"])

    assert first.claims["jti"] != second.claims["jti"]


# ── keys ────────────────────────────────────────────────────────────────────


async def test_a_configured_key_survives_a_restart() -> None:
    """Two instances with one key: a token from the first verifies against the second."""
    pem = ECKey.generate_key("P-256", private=True).as_pem(private=True).decode()
    settings = auth_settings(signing_key=pem)

    async for first in running(create_app(settings)):
        token = (await ask(first)).json()["access_token"]
    async for second in running(create_app(settings)):
        keys = KeySet.import_key_set((await second.get("/.well-known/jwks.json")).json())
        assert jwt.decode(token, keys, algorithms=["ES256"]).claims["sub"] == AGENT_ID


async def test_a_generated_key_does_not_survive_a_restart() -> None:
    """The flip side, stated on purpose: with no key configured, a restart revokes all."""
    async for first in running(create_app(auth_settings())):
        first_kid = (await first.get("/.well-known/jwks.json")).json()["keys"][0]["kid"]
    async for second in running(create_app(auth_settings())):
        second_kid = (await second.get("/.well-known/jwks.json")).json()["keys"][0]["kid"]

    assert first_kid != second_kid


def test_the_key_id_is_derived_from_the_key() -> None:
    key = ECKey.generate_key("P-256", private=True)

    assert SigningKey(key).kid == SigningKey(key).kid == key.thumbprint()


def test_a_public_key_cannot_be_used_to_sign() -> None:
    public = ECKey.generate_key("P-256", private=True).as_pem(private=False).decode()

    with pytest.raises(ValueError, match="private key"):
        SigningKey.from_pem(public)


def test_only_p256_is_accepted() -> None:
    with pytest.raises(ValueError, match="P-256"):
        SigningKey(ECKey.generate_key("P-384", private=True))


@pytest.mark.parametrize("empty", ["", "   "])
def test_an_empty_setting_means_unset(empty: str) -> None:
    """Compose turns an unset variable into an empty string.

    An empty client secret taken literally is a client anybody can authenticate as
    by sending an empty password, which is the case worth a test.
    """
    settings = auth_settings(agent_client_secret=empty, signing_key=empty)

    assert settings.agent_client_secret is None
    assert settings.signing_key is None


async def test_an_empty_secret_cannot_authenticate() -> None:
    async for client in running(create_app(auth_settings(agent_client_secret=""))):
        response = await ask(client, auth=BasicAuth(AGENT_ID, ""))

        assert response.status_code == 401


# ── basic auth parsing ──────────────────────────────────────────────────────


def basic(raw: str) -> str:
    return "Basic " + base64.b64encode(raw.encode()).decode()


def test_basic_auth_is_decoded_after_splitting() -> None:
    """RFC 6749 section 2.3.1: a colon in a secret is percent-encoded first."""
    assert parse_basic_auth(basic("client:se%3Acret")) == ("client", "se:cret")


@pytest.mark.parametrize(
    "header",
    [None, "", "Bearer abc", "Basic", "Basic !!!not-base64!!!", basic("no-colon-here")],
)
def test_malformed_basic_auth_is_none(header: str | None) -> None:
    assert parse_basic_auth(header) is None
