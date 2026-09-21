"""Registered clients.

Pre-registered, in code, with secrets from the environment. Dynamic registration
is on Deskpilot's backlog; until then, a client exists because somebody decided it
should, and what it may be given is written down next to it.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
from dataclasses import dataclass
from urllib.parse import unquote

from paywisp.auth_server.config import SCOPE_PAYMENTS_READ, Settings

GRANT_CLIENT_CREDENTIALS = "client_credentials"


@dataclass(frozen=True)
class Client:
    client_id: str
    # SHA-256 of the secret. Client secrets are long random strings, not chosen
    # passwords, so there is no dictionary to slow down and no need for a slow hash;
    # the digest exists so a memory dump or a log line cannot hand the secret out.
    secret_digest: str
    grant_types: frozenset[str]
    # The ceiling. A client may ask for less than this, never more.
    allowed_scopes: frozenset[str]

    def verify_secret(self, secret: str) -> bool:
        return hmac.compare_digest(digest(secret), self.secret_digest)


def digest(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def registered_clients(settings: Settings) -> dict[str, Client]:
    """Every client this server knows, keyed by id."""
    clients: dict[str, Client] = {}
    if settings.agent_client_secret is not None:
        clients[settings.agent_client_id] = Client(
            client_id=settings.agent_client_id,
            secret_digest=digest(settings.agent_client_secret.get_secret_value()),
            grant_types=frozenset({GRANT_CLIENT_CREDENTIALS}),
            # Read only, and this is the line that makes it true. Deskpilot's agent
            # asking only for read is a promise; this client being unable to receive
            # write is a property. A leaked agent secret cannot move money.
            allowed_scopes=frozenset({SCOPE_PAYMENTS_READ}),
        )
    return clients


def parse_basic_auth(header: str | None) -> tuple[str, str] | None:
    """Read client credentials from an HTTP Basic header.

    RFC 6749 section 2.3.1 says the id and secret are form-urlencoded before being
    joined and base64-encoded, so they are decoded after splitting. Skipping that
    step works until a secret contains a colon or a percent sign.
    """
    if header is None:
        return None
    scheme, _, encoded = header.partition(" ")
    if scheme.lower() != "basic" or not encoded:
        return None
    try:
        decoded = base64.b64decode(encoded, validate=True).decode()
    except (binascii.Error, UnicodeDecodeError):
        return None
    client_id, separator, secret = decoded.partition(":")
    if not separator:
        return None
    return unquote(client_id), unquote(secret)
