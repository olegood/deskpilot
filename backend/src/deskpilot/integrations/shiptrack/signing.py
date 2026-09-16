"""Signing a request for ShipTrack.

Deliberately a second implementation of the same scheme rather than a shared
module. ShipTrack is another company: in life its signing code would be in a
document, not an import. Writing it twice is what makes the tests meaningful -
a shared helper cannot disagree with itself, so it can never catch the case where
one side changes and the other does not.
"""

from __future__ import annotations

import hashlib
import hmac
import time
import uuid

HEADER_KEY = "X-ShipTrack-Key"
HEADER_TIMESTAMP = "X-ShipTrack-Timestamp"
HEADER_NONCE = "X-ShipTrack-Nonce"
HEADER_SIGNATURE = "X-ShipTrack-Signature"


def signed_headers(
    secret: str, key_id: str, method: str, path: str, body: bytes = b""
) -> dict[str, str]:
    """Build the four headers for one request.

    A fresh timestamp and a fresh nonce every time this is called, which matters
    for retries: reusing them would either fall outside the carrier's clock window
    or be refused as a replay, so a retry would fail for a reason that has nothing
    to do with why the first attempt failed.
    """
    timestamp = str(int(time.time()))
    nonce = uuid.uuid4().hex
    digest = hashlib.sha256(body).hexdigest()
    message = "\n".join([method.upper(), path, timestamp, nonce, digest])
    signature = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()
    return {
        HEADER_KEY: key_id,
        HEADER_TIMESTAMP: timestamp,
        HEADER_NONCE: nonce,
        HEADER_SIGNATURE: signature,
    }
