"""Making the carrier misbehave on purpose."""

from __future__ import annotations

import asyncio
import logging
import random

from shiptrack.config import ChaosSettings

logger = logging.getLogger(__name__)


class ChaosMonkey:
    """Decides what to do to a request, and does it.

    Seeded random, so a test can arrange a failure and get the same one twice.
    Unseeded in production, where "every third request" would be a pattern a
    client could accidentally rely on.
    """

    def __init__(self, settings: ChaosSettings, seed: int | None = None) -> None:
        self.settings = settings
        self._random = random.Random(seed)  # noqa: S311 - not used for anything secret

    def should_fail(self) -> bool:
        return self._random.random() < self.settings.error_rate

    def should_hang(self) -> bool:
        return self._random.random() < self.settings.hang_rate

    async def delay(self) -> None:
        if self.settings.latency_ms:
            await asyncio.sleep(self.settings.latency_ms / 1000)

    async def hang(self) -> None:
        """Never answer.

        The failure worth testing against. A 500 is obvious; a connection that
        stays open looks like a slow response until somebody's timeout decides
        otherwise, and a client without one waits for ever.
        """
        logger.warning("hanging for %.0fs on purpose", self.settings.hang_seconds)
        await asyncio.sleep(self.settings.hang_seconds)
