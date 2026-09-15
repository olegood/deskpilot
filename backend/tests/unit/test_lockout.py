"""Unit tests for lockout arithmetic. No database."""

from datetime import UTC, datetime, timedelta

from deskpilot.auth.lockout import (
    is_locked,
    lockout_duration,
    record_failure,
    record_success,
)
from deskpilot.config import AuthSettings
from deskpilot.db.models import User, UserRole

SETTINGS = AuthSettings(
    jwt_secret="test-signing-key",
    max_failed_logins=5,
    lockout_seconds=60,
    max_lockout_seconds=3600,
)


def user(failures: int = 0, locked_until: datetime | None = None) -> User:
    return User(
        id=7,
        email="noah.kim@example.com",
        password_hash="unused",
        full_name="Noah Kim",
        role=UserRole.CUSTOMER,
        failed_logins=failures,
        locked_until=locked_until,
    )


def test_a_fresh_account_is_not_locked() -> None:
    assert not is_locked(user())


def test_an_account_locked_until_the_future_is_locked() -> None:
    assert is_locked(user(locked_until=datetime.now(UTC) + timedelta(minutes=1)))


def test_a_lockout_that_has_passed_no_longer_applies() -> None:
    """Expiry is by time, so nothing has to run to unlock an account."""
    assert not is_locked(user(locked_until=datetime.now(UTC) - timedelta(seconds=1)))


def test_failures_below_the_threshold_do_not_lock() -> None:
    person = user()

    for _ in range(SETTINGS.max_failed_logins - 1):
        record_failure(person, SETTINGS)

    assert person.failed_logins == 4
    assert not is_locked(person)


def test_the_threshold_failure_locks_the_account() -> None:
    person = user()

    for _ in range(SETTINGS.max_failed_logins):
        record_failure(person, SETTINGS)

    assert is_locked(person)


def test_the_lockout_doubles_with_each_further_failure() -> None:
    """Doubling is what makes sustained guessing expensive."""
    assert lockout_duration(5, SETTINGS) == timedelta(seconds=60)
    assert lockout_duration(6, SETTINGS) == timedelta(seconds=120)
    assert lockout_duration(7, SETTINGS) == timedelta(seconds=240)


def test_the_lockout_is_capped() -> None:
    assert lockout_duration(100, SETTINGS) == timedelta(seconds=SETTINGS.max_lockout_seconds)


def test_the_first_lockout_is_short_because_the_usual_cause_is_a_typo() -> None:
    assert lockout_duration(SETTINGS.max_failed_logins, SETTINGS) <= timedelta(minutes=5)


def test_success_clears_the_counter_and_the_lock() -> None:
    person = user(failures=9, locked_until=datetime.now(UTC) + timedelta(hours=1))

    record_success(person)

    assert person.failed_logins == 0
    assert person.locked_until is None
    assert not is_locked(person)
