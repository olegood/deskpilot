"""Verifying a signed request.

The checks are ordered so the cheap ones come first, and so the expensive one -
recomputing the HMAC - is not done for a request that was never going to be
accepted.
"""

from __future__ import annotations

import logging
import time
from collections import OrderedDict

from fastapi import Request

from shiptrack import signing
from shiptrack.config import Settings

logger = logging.getLogger(__name__)

# One message for every way a request can fail to authenticate. Saying "bad
# signature" rather than "unknown key" tells whoever is probing which half they
# got right.
REJECTED = "The request could not be authenticated."


class AuthFailed(Exception):
    """Raised when a request is not properly signed."""

    def __init__(self, reason: str) -> None:
        super().__init__(REJECTED)
        # The real reason, for the log. Not returned.
        self.reason = reason


class NonceStore:
    """Remembers recently seen nonces, so a captured request works only once.

    Bounded, oldest evicted first. The bound has to outlast the skew window or a
    burst of traffic can push a nonce out early and make it replayable again -
    which is why the capacity and the window are settings that are meant to be
    read together.
    """

    def __init__(self, capacity: int) -> None:
        self.capacity = capacity
        self._seen: OrderedDict[str, float] = OrderedDict()

    def remember(self, nonce: str) -> bool:
        """Record a nonce. False if it had been seen already."""
        if nonce in self._seen:
            return False
        self._seen[nonce] = time.time()
        while len(self._seen) > self.capacity:
            self._seen.popitem(last=False)
        return True


async def verify(request: Request, settings: Settings, nonces: NonceStore) -> None:
    """Check a request's signature, or raise AuthFailed."""
    if settings.secret is None:
        raise AuthFailed("SHIPTRACK_SECRET is not configured")

    key = request.headers.get(signing.HEADER_KEY)
    timestamp = request.headers.get(signing.HEADER_TIMESTAMP)
    nonce = request.headers.get(signing.HEADER_NONCE)
    provided = request.headers.get(signing.HEADER_SIGNATURE)
    if key is None or timestamp is None or nonce is None or provided is None:
        # Spelled out rather than `all(...)`, which the type checker cannot use to
        # narrow four separate optionals.
        raise AuthFailed("a signing header is missing")

    if key != settings.key_id:
        raise AuthFailed(f"unknown key id {key!r}")

    try:
        skew = abs(time.time() - float(timestamp))
    except ValueError as exc:
        raise AuthFailed(f"timestamp {timestamp!r} is not a number") from exc
    if skew > settings.max_skew_seconds:
        # Both directions. A timestamp far in the future is as suspicious as one
        # far in the past, and allowing it would widen the replay window.
        raise AuthFailed(f"timestamp is {skew:.0f}s away from now")

    body = await request.body()
    expected = signing.sign(
        settings.secret.get_secret_value(),
        request.method,
        request.url.path,
        timestamp,
        nonce,
        body,
    )
    if not signing.matches(expected, provided):
        raise AuthFailed("signature does not match")

    # Last, and only for a request that was otherwise valid. Recording the nonce
    # of a request that failed for another reason would let somebody burn nonces
    # they never legitimately used.
    if not nonces.remember(nonce):
        raise AuthFailed(f"nonce {nonce!r} has been used already")
