"""Unit tests for password hashing and policy. No database.

These run at 4 bcrypt rounds. Twelve is right in production and far too slow for a
test suite that hashes dozens of times.
"""

import time

import pytest

from deskpilot.auth.passwords import (
    DUMMY_HASH,
    MAX_PASSWORD_BYTES,
    MIN_PASSWORD_LENGTH,
    PasswordError,
    check_password_policy,
    hash_password,
    verify_password,
    waste_time_like_a_real_check,
)

GOOD = "correct horse battery staple"
ROUNDS = 4


def test_a_password_verifies_against_its_own_hash() -> None:
    assert verify_password(GOOD, hash_password(GOOD, rounds=ROUNDS))


def test_a_wrong_password_does_not_verify() -> None:
    assert not verify_password("something else entirely", hash_password(GOOD, rounds=ROUNDS))


def test_the_same_password_hashes_differently_every_time() -> None:
    """Each hash carries its own salt, so two identical passwords never collide."""
    first = hash_password(GOOD, rounds=ROUNDS)
    second = hash_password(GOOD, rounds=ROUNDS)

    assert first != second
    assert verify_password(GOOD, first)
    assert verify_password(GOOD, second)


def test_the_hash_is_bcrypt_and_records_its_cost() -> None:
    assert hash_password(GOOD, rounds=ROUNDS).startswith("$2b$04$")


def test_the_plaintext_never_appears_in_the_hash() -> None:
    assert GOOD not in hash_password(GOOD, rounds=ROUNDS)


def test_verify_returns_false_for_a_corrupted_hash() -> None:
    """A caller must not be able to tell a broken hash from a wrong password."""
    assert not verify_password(GOOD, "not-a-bcrypt-hash")
    assert not verify_password(GOOD, "")


def test_a_short_password_is_rejected() -> None:
    with pytest.raises(PasswordError, match=f"at least {MIN_PASSWORD_LENGTH}"):
        check_password_policy("x" * (MIN_PASSWORD_LENGTH - 1))


def test_a_password_over_72_bytes_is_rejected_rather_than_truncated() -> None:
    """bcrypt ignores anything past 72 bytes, so two long passwords could collide."""
    with pytest.raises(PasswordError, match="at most 72 bytes"):
        check_password_policy("x" * (MAX_PASSWORD_BYTES + 1))


def test_the_limit_is_counted_in_bytes_not_characters() -> None:
    """40 accented characters are 80 bytes in UTF-8, and would be truncated."""
    accented = "é" * 40

    assert len(accented) < MAX_PASSWORD_BYTES
    with pytest.raises(PasswordError, match="at most 72 bytes"):
        check_password_policy(accented)


def test_exactly_72_bytes_is_allowed() -> None:
    check_password_policy("x" * MAX_PASSWORD_BYTES)


def test_a_common_password_is_rejected() -> None:
    with pytest.raises(PasswordError, match="too common"):
        check_password_policy("123456789012")


def test_a_password_equal_to_the_email_is_rejected() -> None:
    with pytest.raises(PasswordError, match="not be the email"):
        check_password_policy("noah.kim@example.com", email="Noah.Kim@example.com")


def test_surrounding_whitespace_is_rejected() -> None:
    with pytest.raises(PasswordError, match="whitespace"):
        check_password_policy(f" {GOOD} ")


def test_every_problem_is_reported_at_once() -> None:
    with pytest.raises(PasswordError) as caught:
        check_password_policy(" short ")

    message = str(caught.value)
    assert "at least" in message
    assert "whitespace" in message


def test_hashing_enforces_the_policy_too() -> None:
    """The policy cannot be skipped by calling hash_password directly."""
    with pytest.raises(PasswordError):
        hash_password("short", rounds=ROUNDS)


def test_the_dummy_hash_is_a_real_hash_that_nothing_matches() -> None:
    assert DUMMY_HASH.startswith("$2b$12$")
    assert not verify_password("", DUMMY_HASH)
    assert not verify_password(GOOD, DUMMY_HASH)


@pytest.mark.slow
def test_the_unknown_account_path_costs_about_as_much_as_a_real_check() -> None:
    """A fast failure for an unknown address is how account enumeration works.

    Timing on a shared machine is noisy, so this only asserts the same order of
    magnitude, not equality.
    """
    real = hash_password(GOOD, rounds=12)

    started = time.perf_counter()
    verify_password("wrong but plausible", real)
    real_seconds = time.perf_counter() - started

    started = time.perf_counter()
    waste_time_like_a_real_check()
    dummy_seconds = time.perf_counter() - started

    assert 0.25 < dummy_seconds / real_seconds < 4.0
