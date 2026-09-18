"""Verifying a callback from the carrier.

The mirror image of `signing.py`. It is worth noticing that this is the direction
people forget: a team that signs its outbound requests carefully will often accept
a webhook on the strength of it arriving at a secret-looking URL, which is a
password sitting in every proxy log between here and the sender.

A webhook is an unauthenticated POST from the internet until it is verified.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from collections import OrderedDict

logger = logging.getLogger(__name__)

HEADER_KEY = "X-ShipTrack-Key"
HEADER_TIMESTAMP = "X-ShipTrack-Timestamp"
HEADER_NONCE = "X-ShipTrack-Nonce"
HEADER_SIGNATURE = "X-ShipTrack-Signature"

# One message for every way a callback can be refused, for the same reason the
# carrier gives one: a caller should not learn which half they got right.
REJECTED = "This callback could not be verified."


class WebhookRejected(Exception):
    """Raised when a callback is not properly signed."""

    def __init__(self, reason: str) -> None:
        super().__init__(REJECTED)
        # For the log, not for the sender.
        self.reason = reason


class SeenNonces:
    """Bounded memory of recent nonces.

    In process, like the carrier's. It works because the window it has to cover is
    the skew window, not for ever - and it has to be larger than the traffic in
    that window or a burst can evict a nonce early and make it replayable again.
    """

    def __init__(self, capacity: int) -> None:
        self.capacity = capacity
        self._seen: OrderedDict[str, float] = OrderedDict()

    def remember(self, nonce: str) -> bool:
        if nonce in self._seen:
            return False
        self._seen[nonce] = time.time()
        while len(self._seen) > self.capacity:
            self._seen.popitem(last=False)
        return True


def verify_callback(
    *,
    secret: str,
    key_id: str,
    path: str,
    body: bytes,
    headers: dict[str, str],
    nonces: SeenNonces,
    max_skew_seconds: int,
) -> None:
    """Check a callback, or raise WebhookRejected.

    Same order as the carrier's own check, and for the same reasons: cheap checks
    first, and the nonce spent only by a caller who already proved they hold the
    secret.
    """
    lower = {name.lower(): value for name, value in headers.items()}
    provided_key = lower.get(HEADER_KEY.lower())
    timestamp = lower.get(HEADER_TIMESTAMP.lower())
    nonce = lower.get(HEADER_NONCE.lower())
    signature = lower.get(HEADER_SIGNATURE.lower())
    if provided_key is None or timestamp is None or nonce is None or signature is None:
        raise WebhookRejected("a signing header is missing")
    if provided_key != key_id:
        raise WebhookRejected(f"unknown key id {provided_key!r}")

    try:
        skew = abs(time.time() - float(timestamp))
    except ValueError as exc:
        raise WebhookRejected(f"timestamp {timestamp!r} is not a number") from exc
    if skew > max_skew_seconds:
        raise WebhookRejected(f"timestamp is {skew:.0f}s away from now")

    message = "\n".join(["POST", path, timestamp, nonce, hashlib.sha256(body).hexdigest()])
    expected = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise WebhookRejected("signature does not match")

    if not nonces.remember(nonce):
        raise WebhookRejected(f"nonce {nonce!r} has been used already")
