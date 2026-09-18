"""Telling Deskpilot when a parcel moves.

The same signing scheme as the inbound API, in the other direction and with a
**different secret**. One secret for requests in, another for requests out: a leak
of the key Deskpilot uses to ask questions should not also let somebody forge
answers, and the two have different blast radii.

Delivery is best effort. A carrier that blocks on its customer's availability is a
carrier that falls over when its customer does, so a failed callback is logged and
dropped. Deskpilot polls for truth; the webhook is only ever a hint that polling
would be worth doing sooner.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
import uuid
from typing import Any

import httpx

logger = logging.getLogger(__name__)

HEADER_KEY = "X-ShipTrack-Key"
HEADER_TIMESTAMP = "X-ShipTrack-Timestamp"
HEADER_NONCE = "X-ShipTrack-Nonce"
HEADER_SIGNATURE = "X-ShipTrack-Signature"


def sign_callback(secret: str, key_id: str, path: str, body: bytes) -> dict[str, str]:
    """Headers for one outbound callback."""
    timestamp = str(int(time.time()))
    nonce = uuid.uuid4().hex
    message = "\n".join(["POST", path, timestamp, nonce, hashlib.sha256(body).hexdigest()])
    return {
        HEADER_KEY: key_id,
        HEADER_TIMESTAMP: timestamp,
        HEADER_NONCE: nonce,
        HEADER_SIGNATURE: hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest(),
        "Content-Type": "application/json",
    }


class WebhookSender:
    """Posts an event to whoever asked to be told."""

    def __init__(
        self,
        url: str | None,
        secret: str | None,
        key_id: str,
        timeout_s: float = 5.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.url = url
        self.secret = secret
        self.key_id = key_id
        self.timeout_s = timeout_s
        self._client = client

    @property
    def configured(self) -> bool:
        return bool(self.url and self.secret)

    async def send(self, event: dict[str, Any]) -> bool:
        """Deliver one event. False if it did not get through."""
        url, secret = self.url, self.secret
        if url is None or secret is None:
            logger.debug("no webhook configured; dropping %s", event.get("event"))
            return False

        body = httpx.Request("POST", url, json=event).content
        path = httpx.URL(url).path
        headers = sign_callback(secret, self.key_id, path, body)
        client = self._client or httpx.AsyncClient(timeout=self.timeout_s)
        try:
            response = await client.post(url, content=body, headers=headers)
        except httpx.HTTPError as exc:
            # Best effort, and said so. A carrier that retried into a customer
            # that is down would turn one outage into two.
            logger.warning("webhook delivery failed: %r", exc)
            return False
        finally:
            if self._client is None:
                await client.aclose()
        if response.status_code >= 400:
            logger.warning("webhook rejected with %d", response.status_code)
            return False
        return True
