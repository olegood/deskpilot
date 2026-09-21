"""Minting access tokens.

The format is RFC 9068, the JWT profile for OAuth access tokens. The parts that
matter for security, and why each one is there:

- ``typ: at+jwt`` in the header, so a resource server can tell an access token from
  any other JWT this issuer signs. Without it, an ID token or a signed document
  could be presented as an access token and pass a signature check.
- ``aud`` naming exactly one resource, so a token issued for the MCP server cannot
  be replayed against some other service that trusts the same issuer.
- ``scope``, which the resource server checks per operation. The token proves what
  the client was *allowed*, not merely who it is.
- ``exp`` a few minutes out, because a bearer token cannot be recalled.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

from joserfc import jwt

from paywisp.auth_server.keys import ALGORITHM, SigningKey

ACCESS_TOKEN_TYPE = "at+jwt"  # noqa: S105 - a media type, not a credential


@dataclass(frozen=True)
class IssuedToken:
    access_token: str
    expires_in: int
    scope: str


def issue_access_token(
    key: SigningKey,
    *,
    issuer: str,
    subject: str,
    client_id: str,
    audience: str,
    scopes: frozenset[str],
    lifetime_seconds: int,
) -> IssuedToken:
    now = int(time.time())
    # Sorted, so the same grant always produces the same scope string. The order
    # carries no meaning, and a stable form is easier to read in a log.
    scope = " ".join(sorted(scopes))
    claims = {
        "iss": issuer,
        "sub": subject,
        "aud": audience,
        "client_id": client_id,
        "scope": scope,
        "iat": now,
        "exp": now + lifetime_seconds,
        "jti": uuid.uuid4().hex,
    }
    header = {"alg": ALGORITHM, "kid": key.kid, "typ": ACCESS_TOKEN_TYPE}
    return IssuedToken(
        access_token=jwt.encode(header, claims, key.private),
        expires_in=lifetime_seconds,
        scope=scope,
    )
