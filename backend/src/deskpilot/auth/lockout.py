"""Slowing down password guessing.

After a handful of consecutive failures an account is locked for a short time, and
the lockout doubles with each further failure. The first lockout is a minute,
because the usual cause of five wrong passwords is somebody mistyping, not an
attack.

Locking an account is a trade. It stops guessing, and it also lets anybody who
knows an email address lock its owner out by failing on purpose. That is why the
first lockout is short and why the real answer, rate limiting by source address,
arrives with the web milestone, where there is a source address to limit by.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from deskpilot.config import AuthSettings
from deskpilot.db.models import User

logger = logging.getLogger(__name__)


def now() -> datetime:
    return datetime.now(UTC)


def is_locked(user: User) -> bool:
    """True while the account is inside a lockout window."""
    return user.locked_until is not None and user.locked_until > now()


def lockout_duration(failures: int, settings: AuthSettings) -> timedelta:
    """How long to lock, given how many failures there have been.

    Doubles per failure past the threshold, capped. Doubling matters more than the
    starting value: it makes sustained guessing cost exponentially more while a
    single fat-fingered evening costs a minute.
    """
    over = max(0, failures - settings.max_failed_logins)
    seconds = min(settings.lockout_seconds * (2**over), settings.max_lockout_seconds)
    return timedelta(seconds=seconds)


def record_failure(user: User, settings: AuthSettings) -> None:
    """Count a failed attempt and lock the account if it has had too many.

    Does not commit. The caller must, and must do so even though the login failed,
    or the count never survives.
    """
    user.failed_logins += 1
    if user.failed_logins >= settings.max_failed_logins:
        duration = lockout_duration(user.failed_logins, settings)
        user.locked_until = now() + duration
        logger.warning(
            "locked account %s for %ss after %s failed logins",
            user.id,
            int(duration.total_seconds()),
            user.failed_logins,
        )


def record_success(user: User) -> None:
    """Clear the counter after a successful login. Does not commit."""
    user.failed_logins = 0
    user.locked_until = None
