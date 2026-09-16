"""Asking the carrier where a parcel is.

Three layers, in this order, and the order matters:

1. a **timeout**, so a request that never answers becomes a failure
2. **retries**, so a blip does not become an error the customer sees
3. a **circuit breaker**, so a real outage does not turn into three timeouts per
   request and a queue of traffic aimed at a service already struggling

Without the timeout the other two never trigger. Without the breaker the retries
make an outage worse rather than better.
"""

from __future__ import annotations

import asyncio
import logging
import random
from types import TracebackType
from typing import Any, Self

import httpx

from deskpilot.config import ShipTrackSettings
from deskpilot.integrations.shiptrack import signing
from deskpilot.integrations.shiptrack.breaker import CircuitBreaker
from deskpilot.integrations.shiptrack.errors import CarrierError, CarrierUnavailable

logger = logging.getLogger(__name__)

# 5xx means the carrier is having a problem, and a later attempt might land
# somewhere healthier. 4xx means we asked wrongly, and asking again produces the
# same answer.
RETRYABLE_STATUS = frozenset({500, 502, 503, 504})


class ShipTrackClient:
    """One client per process, holding one connection pool and one breaker."""

    def __init__(
        self,
        settings: ShipTrackSettings,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if settings.secret is None:
            raise CarrierError("DESKPILOT_SHIPTRACK__SECRET is not set")
        self.settings = settings
        # Unwrapped once, in the one place that checked it.
        self._secret = settings.secret.get_secret_value()
        self.breaker = CircuitBreaker(
            settings.breaker_threshold, settings.breaker_reset_seconds, name="shiptrack"
        )
        self._client = httpx.AsyncClient(
            base_url=str(settings.base_url).rstrip("/"),
            timeout=httpx.Timeout(
                connect=settings.connect_timeout_s,
                read=settings.read_timeout_s,
                write=settings.read_timeout_s,
                pool=settings.connect_timeout_s,
            ),
            transport=transport,
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def track(self, tracking_number: str) -> dict[str, Any] | None:
        """Where a parcel is, or None if the carrier has never heard of it."""
        path = f"/api/shipments/{tracking_number.strip().upper()}"
        response = await self._get(path)
        if response.status_code == httpx.codes.NOT_FOUND:
            return None
        if response.status_code != httpx.codes.OK:
            # A 401 lands here: the key is wrong, and that is our problem to fix,
            # not something to retry or to trip the breaker over.
            raise CarrierError(f"the carrier answered {response.status_code}")
        body: dict[str, Any] = response.json()
        return body

    async def _get(self, path: str) -> httpx.Response:
        if not self.breaker.allows():
            # Failing here costs nothing and asks nothing of a service that is
            # already in trouble.
            raise CarrierUnavailable("the carrier is unavailable")

        last: Exception | None = None
        for attempt in range(self.settings.retries + 1):
            if attempt:
                await asyncio.sleep(self._backoff(attempt))
            try:
                # Signed inside the loop: every attempt gets its own timestamp and
                # nonce, or a retry is refused as stale or as a replay.
                response = await self._client.get(
                    path,
                    headers=signing.signed_headers(self._secret, self.settings.key_id, "GET", path),
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                # A hang arrives here, as a read timeout. This is the line that
                # turns "never answers" into "failed".
                logger.warning("carrier request failed (attempt %d): %r", attempt + 1, exc)
                last = exc
                continue

            if response.status_code in RETRYABLE_STATUS:
                logger.warning(
                    "carrier answered %d (attempt %d)", response.status_code, attempt + 1
                )
                last = CarrierUnavailable(f"the carrier answered {response.status_code}")
                continue

            # Anything else is an answer, including a 404 and a 401. The breaker
            # cares about whether the carrier is reachable, not whether we liked
            # what it said.
            self.breaker.record_success()
            return response

        self.breaker.record_failure()
        raise CarrierUnavailable("the carrier is unavailable") from last

    def _backoff(self, attempt: int) -> float:
        """Exponential, with jitter.

        The jitter is not decoration. Without it every client that failed at the
        same moment retries at the same moment, and the supplier gets a second
        wave exactly as it comes back.
        """
        base: float = self.settings.backoff_seconds * float(2 ** (attempt - 1))
        jitter: float = random.random()  # noqa: S311 - timing, not security
        return base * (0.5 + jitter)
