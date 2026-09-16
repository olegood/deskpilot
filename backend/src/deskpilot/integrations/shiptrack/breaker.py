"""A circuit breaker.

Retries help when a failure is a blip. They make things worse when the carrier is
actually down: every request spends its full timeout budget three times over, so a
dead supplier turns into a slow application, and the load arrives just as the
supplier is trying to recover.

The breaker is the answer. After enough consecutive failures it stops asking and
fails immediately, then lets one request through after a while to find out whether
things have improved.
"""

from __future__ import annotations

import logging
import time
from enum import StrEnum

logger = logging.getLogger(__name__)


class State(StrEnum):
    # Asking normally.
    CLOSED = "closed"
    # Not asking. Everything fails immediately.
    OPEN = "open"
    # Letting one request through to find out.
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Per-service, and not shared between services.

    One breaker for the carrier and another for payments: a carrier outage should
    not stop refunds, and the point of tripping is to protect the caller from a
    particular supplier rather than from suppliers in general.
    """

    def __init__(self, threshold: int, reset_seconds: float, name: str = "carrier") -> None:
        self.threshold = threshold
        self.reset_seconds = reset_seconds
        self.name = name
        self._failures = 0
        self._opened_at: float | None = None

    @property
    def state(self) -> State:
        if self._opened_at is None:
            return State.CLOSED
        if time.monotonic() - self._opened_at >= self.reset_seconds:
            return State.HALF_OPEN
        return State.OPEN

    def allows(self) -> bool:
        """Whether to try at all."""
        return self.state is not State.OPEN

    def record_success(self) -> None:
        """A success closes the circuit, from any state.

        One success is enough on purpose. A half-open probe that worked is the
        evidence the breaker was waiting for, and demanding several would keep
        real traffic out for longer than the outage lasted.
        """
        if self._opened_at is not None:
            logger.info("%s circuit closed again", self.name)
        self._failures = 0
        self._opened_at = None

    def record_failure(self) -> None:
        """A failure counts, and a failed probe re-opens the circuit."""
        self._failures += 1
        if self.state is State.HALF_OPEN or self._failures >= self.threshold:
            if self._opened_at is None:
                logger.warning(
                    "%s circuit opened after %d consecutive failures", self.name, self._failures
                )
            self._opened_at = time.monotonic()
