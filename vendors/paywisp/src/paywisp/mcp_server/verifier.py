"""Checking a bearer token, the way a resource server should.

The MCP SDK asks one question - "is this token good, and what does it allow?" -
and leaves the answer to us. Every check below closes a specific hole, and the
order matters: the cheap structural checks come first, the network fetch only
when a key is genuinely unknown, and the signature before any claim is believed.

Nothing is shared with the authorization server except what it publishes. The
keys come from its JWKS over HTTP, which is what keeps swapping it for Keycloak a
configuration change (Deskpilot's D-138).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import KeySet
from joserfc.jws import extract_compact
from mcp.server.auth.provider import AccessToken

from paywisp.mcp_server.config import Settings

logger = logging.getLogger(__name__)

# Pinned. The algorithm comes from this list, never from the token's own header:
# trusting the header is how `alg: none` gets accepted, and how a public key gets
# used as an HMAC secret to forge a token.
ALGORITHMS = ["ES256"]
ACCESS_TOKEN_TYPES = {"at+jwt", "application/at+jwt"}


class TokenRejected(Exception):
    """Why a token was refused. For the log only: the caller gets a flat 401."""


class JWKSTokenVerifier:
    """Implements the SDK's `TokenVerifier` protocol."""

    def __init__(self, settings: Settings, http: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self._http = http or httpx.AsyncClient(timeout=5.0)
        self._owns_http = http is None
        self._keys: KeySet | None = None
        self._fetched_at = 0.0
        # Concurrent requests carrying a new key id should cause one fetch, not one
        # each. The same single-flight idea as Deskpilot's refresh (D-112).
        self._lock = asyncio.Lock()

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def verify_token(self, token: str) -> AccessToken | None:
        """The SDK's hook. None means 401; the reason goes to the log."""
        try:
            return await self._verify(token)
        except TokenRejected as exc:
            logger.info("rejected a token: %s", exc)
            return None

    async def _verify(self, token: str) -> AccessToken:
        header = self._header(token)
        key = await self._key_for(str(header["kid"]))
        try:
            decoded = jwt.decode(token, key, algorithms=ALGORITHMS)
        except JoseError as exc:
            raise TokenRejected(f"signature did not verify: {exc.error}") from exc
        claims = decoded.claims
        self._check_claims(claims)
        return AccessToken(
            token=token,
            client_id=str(claims["client_id"]),
            scopes=str(claims.get("scope", "")).split(),
            expires_at=int(claims["exp"]),
            # What the token says it is for, not what this server is. Filling in our
            # own address here would make the SDK's audience check compare a value
            # with itself and pass everything, which it did until a mutation test
            # showed it (D-147).
            resource=self._resource(claims["aud"]),
            subject=str(claims["sub"]),
            claims=claims,
        )

    def _resource(self, audience: object) -> str | None:
        """The token's audience in the shape the SDK expects: for a list, our entry."""
        if isinstance(audience, str):
            return audience
        if isinstance(audience, list) and self.settings.resource in audience:
            return self.settings.resource
        return None

    def _header(self, token: str) -> dict[str, Any]:
        """Structural checks, before any key is looked up or any network is used."""
        try:
            header: dict[str, Any] = extract_compact(token.encode()).protected
        except (JoseError, ValueError, UnicodeError) as exc:
            raise TokenRejected("not a compact JWS") from exc
        if header.get("alg") not in ALGORITHMS:
            raise TokenRejected(f"algorithm {header.get('alg')!r} is not accepted")
        # RFC 9068. An issuer may sign more than one kind of JWT, and without this
        # any of them could be presented here as an access token (D-142).
        if str(header.get("typ", "")).lower() not in ACCESS_TOKEN_TYPES:
            raise TokenRejected(f"type {header.get('typ')!r} is not an access token")
        if not isinstance(header.get("kid"), str):
            raise TokenRejected("no key id")
        return header

    def _check_claims(self, claims: dict[str, Any]) -> None:
        now = time.time()
        leeway = self.settings.leeway_seconds
        for name in ("iss", "aud", "exp", "sub", "client_id"):
            if name not in claims:
                raise TokenRejected(f"missing claim {name!r}")
        if claims["iss"] != self.settings.issuer:
            raise TokenRejected(f"issuer {claims['iss']!r} is not trusted")
        # One audience, and it must be this server. A list is accepted only if it is
        # exactly this server, because a token valid somewhere else as well is the
        # thing an audience exists to prevent (D-141).
        audience = claims["aud"]
        if audience != self.settings.resource and audience != [self.settings.resource]:
            raise TokenRejected(f"audience {audience!r} is not this server")
        if not isinstance(claims["exp"], int | float) or claims["exp"] <= now - leeway:
            raise TokenRejected("expired")
        # Optional, but if present they must be numbers: a claim the check cannot
        # read is not a claim the check may skip.
        for name, failure in (("nbf", "not valid yet"), ("iat", "issued in the future")):
            value = claims.get(name)
            if value is None:
                continue
            if not isinstance(value, int | float) or value > now + leeway:
                raise TokenRejected(failure if isinstance(value, int | float) else f"bad {name}")

    async def _key_for(self, kid: str) -> KeySet:
        """The key set, containing `kid`, fetched at most once per interval.

        An unknown key id is how a rotated key shows up, so it triggers a refetch.
        It is also something anybody can put in a forged token, so refetches have a
        floor: otherwise a stream of made-up ids becomes a stream of requests to the
        authorization server, sent by this one on the attacker's behalf.
        """
        if self._keys is not None and _has(self._keys, kid):
            return self._keys
        async with self._lock:
            if self._keys is not None and _has(self._keys, kid):
                return self._keys
            elapsed = time.monotonic() - self._fetched_at
            if self._keys is not None and elapsed < self.settings.jwks_min_refresh_seconds:
                raise TokenRejected(f"unknown key id {kid!r}, and the key set is fresh")
            self._keys = await self._fetch()
            self._fetched_at = time.monotonic()
        if not _has(self._keys, kid):
            raise TokenRejected(f"unknown key id {kid!r}")
        return self._keys

    async def _fetch(self) -> KeySet:
        url = self.settings.jwks_url or await self._discover_jwks_url()
        try:
            response = await self._http.get(url)
            response.raise_for_status()
            return KeySet.import_key_set(response.json())
        except (httpx.HTTPError, ValueError, JoseError) as exc:
            # Fail closed. A server that cannot fetch keys cannot check signatures,
            # and accepting tokens it cannot check is not an option.
            logger.warning("could not fetch the key set from %s: %r", url, exc)
            raise TokenRejected("the key set is unavailable") from exc

    async def _discover_jwks_url(self) -> str:
        url = f"{self.settings.issuer}/.well-known/oauth-authorization-server"
        try:
            response = await self._http.get(url)
            response.raise_for_status()
            metadata: dict[str, Any] = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("could not read issuer metadata from %s: %r", url, exc)
            raise TokenRejected("the issuer's metadata is unavailable") from exc
        # RFC 8414 section 3.3. Metadata that names a different issuer is either a
        # misconfiguration or somebody else's server; either way its keys are not
        # the ones to trust.
        if metadata.get("issuer") != self.settings.issuer:
            raise TokenRejected("the issuer's metadata names a different issuer")
        return str(metadata["jwks_uri"])


def _has(keys: KeySet, kid: str) -> bool:
    return any(key.kid == kid for key in keys.keys)
