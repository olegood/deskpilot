"""Access token tests, including the attacks a JWT invites. No database."""

from datetime import UTC, datetime, timedelta

import jwt
import pytest

from deskpilot.auth.tokens import (
    AccessClaims,
    TokenError,
    check_version,
    decode_access_token,
    hash_refresh_token,
    issue_access_token,
    new_family_id,
    new_refresh_token,
)
from deskpilot.config import AuthSettings
from deskpilot.db.models import User, UserRole

SECRET = "test-signing-key-not-used-anywhere-real"


def settings(**overrides: object) -> AuthSettings:
    return AuthSettings(jwt_secret=SECRET, **overrides)  # type: ignore[arg-type]


def user(user_id: int = 7, version: int = 1, active: bool = True) -> User:
    return User(
        id=user_id,
        email="noah.kim@example.com",
        password_hash="unused",
        full_name="Noah Kim",
        role=UserRole.CUSTOMER,
        is_active=active,
        token_version=version,
    )


def payload_of(token: str) -> dict[str, object]:
    """Read a token without verifying it, to inspect what was put in it."""
    return jwt.decode(token, options={"verify_signature": False}, audience="deskpilot-api")


# ── the happy path ──────────────────────────────────────────────────────────


def test_a_freshly_issued_token_decodes() -> None:
    claims = decode_access_token(issue_access_token(user(), settings()), settings())

    assert claims.user_id == 7
    assert claims.version == 1
    assert claims.expires_at > datetime.now(UTC)


def test_each_token_has_its_own_id() -> None:
    first = decode_access_token(issue_access_token(user(), settings()), settings())
    second = decode_access_token(issue_access_token(user(), settings()), settings())

    assert first.token_id != second.token_id


def test_the_token_carries_no_permissions() -> None:
    """Anything an authorization decision needs is loaded fresh, not baked in."""
    claims = payload_of(issue_access_token(user(), settings()))

    assert set(claims) == {"sub", "jti", "ver", "iat", "exp", "iss", "aud"}
    assert "role" not in claims
    assert "email" not in claims


# ── attacks ─────────────────────────────────────────────────────────────────


def test_a_token_signed_with_another_key_is_refused() -> None:
    forged = issue_access_token(user(), settings())

    with pytest.raises(TokenError, match="not valid"):
        decode_access_token(forged, AuthSettings(jwt_secret="a completely different key"))


def test_a_tampered_payload_is_refused() -> None:
    token = issue_access_token(user(user_id=7), settings())
    head, _body, signature = token.split(".")
    other = issue_access_token(user(user_id=99), settings()).split(".")[1]

    with pytest.raises(TokenError, match="not valid"):
        decode_access_token(f"{head}.{other}.{signature}", settings())


def test_an_unsigned_token_is_refused() -> None:
    """alg: none. The algorithm is pinned at decode, so the header cannot choose."""
    unsigned = jwt.encode(
        {
            "sub": "7",
            "jti": "x",
            "ver": 1,
            "iat": datetime.now(UTC),
            "exp": datetime.now(UTC) + timedelta(hours=1),
            "iss": "deskpilot",
            "aud": "deskpilot-api",
        },
        key="",
        algorithm="none",
    )

    with pytest.raises(TokenError, match="not valid"):
        decode_access_token(unsigned, settings())


def test_an_expired_token_is_refused() -> None:
    expired = jwt.encode(
        {
            "sub": "7",
            "jti": "x",
            "ver": 1,
            "iat": datetime.now(UTC) - timedelta(hours=2),
            "exp": datetime.now(UTC) - timedelta(hours=1),
            "iss": "deskpilot",
            "aud": "deskpilot-api",
        },
        SECRET,
        algorithm="HS256",
    )

    with pytest.raises(TokenError, match="expired"):
        decode_access_token(expired, settings())


def test_a_token_for_another_audience_is_refused() -> None:
    """A token minted for another service must not be replayable here."""
    elsewhere = issue_access_token(user(), settings(jwt_audience="some-other-api"))

    with pytest.raises(TokenError, match="not valid"):
        decode_access_token(elsewhere, settings())


def test_a_token_from_another_issuer_is_refused() -> None:
    elsewhere = issue_access_token(user(), settings(jwt_issuer="somebody-else"))

    with pytest.raises(TokenError, match="not valid"):
        decode_access_token(elsewhere, settings())


def test_a_token_missing_a_required_claim_is_refused() -> None:
    incomplete = jwt.encode(
        {
            "sub": "7",
            "iat": datetime.now(UTC),
            "exp": datetime.now(UTC) + timedelta(hours=1),
            "iss": "deskpilot",
            "aud": "deskpilot-api",
        },
        SECRET,
        algorithm="HS256",
    )

    with pytest.raises(TokenError, match="not valid"):
        decode_access_token(incomplete, settings())


@pytest.mark.parametrize("rubbish", ["", "not.a.token", "a.b", "....", "Bearer abc"])
def test_rubbish_is_refused_without_raising_something_else(rubbish: str) -> None:
    with pytest.raises(TokenError):
        decode_access_token(rubbish, settings())


# ── revocation ──────────────────────────────────────────────────────────────


def test_a_token_from_before_a_revocation_is_refused() -> None:
    claims = decode_access_token(issue_access_token(user(version=1), settings()), settings())

    with pytest.raises(TokenError, match="signed out"):
        check_version(claims, user(version=2))


def test_a_token_for_a_disabled_account_is_refused() -> None:
    claims = decode_access_token(issue_access_token(user(), settings()), settings())

    with pytest.raises(TokenError, match="not active"):
        check_version(claims, user(active=False))


def test_a_current_token_passes_the_version_check() -> None:
    claims = decode_access_token(issue_access_token(user(version=3), settings()), settings())

    check_version(claims, user(version=3))


# ── refresh tokens ──────────────────────────────────────────────────────────


def test_refresh_tokens_are_unguessable_and_unique() -> None:
    tokens = {new_refresh_token() for _ in range(100)}

    assert len(tokens) == 100
    assert all(len(token) >= 40 for token in tokens)


def test_the_digest_is_deterministic_and_hides_the_token() -> None:
    token = new_refresh_token()

    assert hash_refresh_token(token) == hash_refresh_token(token)
    assert token not in hash_refresh_token(token)
    assert len(hash_refresh_token(token)) == 64


def test_family_ids_are_unique() -> None:
    assert len({new_family_id() for _ in range(100)}) == 100


def test_claims_are_immutable() -> None:
    claims = AccessClaims(user_id=1, token_id="x", version=1, expires_at=datetime.now(UTC))

    with pytest.raises(AttributeError):
        claims.user_id = 2  # type: ignore[misc]
