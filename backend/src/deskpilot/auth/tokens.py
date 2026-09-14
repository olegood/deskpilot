"""Access tokens and refresh tokens.

An access token is a signed JWT that proves who somebody is for a few minutes. It
carries no permissions: what the holder may do is decided by loading their current
attributes, so a reviewer whose limit was lowered does not keep the old one until
their token expires.

A refresh token is an opaque random string that buys a new access token. It rotates
on every use, and presenting a retired one revokes the whole family.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt

from deskpilot.config import AuthSettings
from deskpilot.db.models import User

logger = logging.getLogger(__name__)

# 256 bits of entropy. Nothing to guess, so no slow hash is needed on the way in.
REFRESH_TOKEN_BYTES = 32


class TokenError(Exception):
    """Raised when a token is missing, malformed, expired, or no longer valid."""


@dataclass(frozen=True)
class AccessClaims:
    """What a verified access token tells us. Deliberately almost nothing."""

    user_id: int
    # Unique per token, so an individual token can be named in a log or a denylist.
    token_id: str
    # The token_version the account had when this was issued.
    version: int
    expires_at: datetime


def now() -> datetime:
    return datetime.now(UTC)


def issue_access_token(user: User, settings: AuthSettings) -> str:
    """Sign a short-lived access token for a user.

    The claims are minimal on purpose: an id, a version, and the standard envelope.
    No role, no email, no approval limit. Anything an authorization decision needs
    is read from the database at the moment of the decision, because a claim baked
    in at login is a snapshot of a permission that may since have been taken away.
    """
    if settings.jwt_secret is None:  # Settings validation normally prevents this.
        raise TokenError("DESKPILOT_AUTH__JWT_SECRET is not set")
    issued = now()
    payload = {
        "sub": str(user.id),
        "jti": str(uuid.uuid4()),
        "ver": user.token_version,
        "iat": issued,
        "exp": issued + timedelta(minutes=settings.access_token_minutes),
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
    }
    return jwt.encode(payload, settings.jwt_secret.get_secret_value(), settings.jwt_algorithm)


def decode_access_token(token: str, settings: AuthSettings) -> AccessClaims:
    """Verify an access token and return its claims, or raise TokenError.

    The algorithm is pinned here rather than taken from the token's own header.
    Trusting the header is how `alg: none` gets accepted, and how an HMAC-signed
    token gets verified against a public key that an attacker already has.
    """
    if settings.jwt_secret is None:
        raise TokenError("DESKPILOT_AUTH__JWT_SECRET is not set")
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            options={"require": ["sub", "jti", "ver", "iat", "exp", "iss", "aud"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("this session has expired") from exc
    except jwt.InvalidTokenError as exc:
        # Everything else - a bad signature, a wrong audience, a missing claim -
        # gets one message. The detail goes to the log, not to the caller.
        logger.info("rejected an access token: %s", exc)
        raise TokenError("this session is not valid") from exc

    try:
        return AccessClaims(
            user_id=int(payload["sub"]),
            token_id=str(payload["jti"]),
            version=int(payload["ver"]),
            expires_at=datetime.fromtimestamp(payload["exp"], tz=UTC),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise TokenError("this session is not valid") from exc


def check_version(claims: AccessClaims, user: User) -> None:
    """Refuse a token issued before the account's tokens were last revoked."""
    if claims.version != user.token_version:
        raise TokenError("this session has been signed out")
    if not user.is_active:
        raise TokenError("this account is not active")


def new_refresh_token() -> str:
    """A fresh opaque refresh token. Never stored; only its digest is."""
    return secrets.token_urlsafe(REFRESH_TOKEN_BYTES)


def hash_refresh_token(token: str) -> str:
    """The digest stored against a refresh token.

    SHA-256 rather than bcrypt: the input is 256 random bits, so there is no
    dictionary to run and no benefit to being slow. Being fast matters, because
    this runs on the lookup path for every refresh.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_family_id() -> str:
    """Identifies every token descended from one login."""
    return uuid.uuid4().hex
