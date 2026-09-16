"""Request signing.

A shared secret and an HMAC over the parts of the request that matter. This is how
a great many payment and carrier APIs authenticate, and it is worth understanding
why each piece is in the signature:

- the **method and path**, so a signature for a read cannot be replayed as a write
- the **timestamp**, so an old capture stops working
- the **nonce**, so a capture cannot be replayed inside the timestamp window
- a **digest of the body**, so the body cannot be swapped for another

Leave any one out and the scheme has a hole. The body digest is the one people
forget, and it is the one that lets an attacker change what a valid signature says.
"""

from __future__ import annotations

import hashlib
import hmac

HEADER_KEY = "X-ShipTrack-Key"
HEADER_TIMESTAMP = "X-ShipTrack-Timestamp"
HEADER_NONCE = "X-ShipTrack-Nonce"
HEADER_SIGNATURE = "X-ShipTrack-Signature"


def body_digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def canonical_request(method: str, path: str, timestamp: str, nonce: str, body: bytes) -> str:
    """The exact string both sides sign.

    Newline separated and fully specified. A scheme that concatenates without a
    separator lets two different requests produce the same string to sign.
    """
    return "\n".join([method.upper(), path, timestamp, nonce, body_digest(body)])


def sign(secret: str, method: str, path: str, timestamp: str, nonce: str, body: bytes) -> str:
    message = canonical_request(method, path, timestamp, nonce, body)
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


def matches(expected: str, provided: str) -> bool:
    """Constant-time comparison.

    `==` on strings returns as soon as two characters differ, and the time that
    takes is measurable over enough requests. It is a small leak and an avoidable
    one.
    """
    return hmac.compare_digest(expected, provided)
