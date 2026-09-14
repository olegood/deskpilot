"""Password hashing and the rules a password has to satisfy.

bcrypt is used directly rather than through passlib, which is unmaintained and
breaks against bcrypt 4.x and later (see docs/decisions.md, D-007).
"""

from __future__ import annotations

import secrets

import bcrypt

# bcrypt hashes at most 72 bytes of input and silently ignores the rest, so two
# different long passwords can share a hash. Rejecting is the only honest option:
# truncating quietly would weaken a password the user believed was strong, and
# pre-hashing with SHA-256 first would work but adds a scheme to get wrong.
MAX_PASSWORD_BYTES = 72

MIN_PASSWORD_LENGTH = 12

# Enough to catch the obvious. A real deployment would check a breach corpus.
COMMON_PASSWORDS = frozenset(
    {
        "password",
        "password1",
        "passw0rd",
        "123456789012",
        "qwertyuiop",
        "letmein12345",
        "administrator",
        "deskpilot",
        "acmegear",
        "changeme123",
    }
)


class PasswordError(ValueError):
    """Raised when a password cannot be accepted. The message is shown to the user."""


def check_password_policy(password: str, email: str | None = None) -> None:
    """Raise PasswordError if the password is not acceptable.

    Checked before hashing, so the caller can report every problem at once rather
    than one per attempt.
    """
    problems: list[str] = []
    if len(password) < MIN_PASSWORD_LENGTH:
        problems.append(f"it must be at least {MIN_PASSWORD_LENGTH} characters")
    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        # Bytes, not characters: accented letters and emoji cost more than one each.
        problems.append(
            f"it must be at most {MAX_PASSWORD_BYTES} bytes, and this is "
            f"{len(password.encode('utf-8'))}"
        )
    if password.strip() != password:
        problems.append("it must not start or end with whitespace")
    if password.lower() in COMMON_PASSWORDS:
        problems.append("it is too common")
    if email and password.lower() == email.lower():
        problems.append("it must not be the email address")
    if problems:
        raise PasswordError("This password will not do: " + ", and ".join(problems) + ".")


def hash_password(password: str, rounds: int = 12) -> str:
    """Hash a password with bcrypt. Rejects anything the policy would not accept."""
    check_password_policy(password)
    salt = bcrypt.gensalt(rounds=rounds)
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    """Check a password against a hash, in constant time.

    Returns False rather than raising on a malformed or over-long input, so a
    caller cannot tell a corrupted hash from a wrong password.
    """
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
    except (ValueError, UnicodeEncodeError):
        return False


def _make_dummy_hash() -> str:
    """A throwaway hash, computed once, for the unknown-account path."""
    return bcrypt.hashpw(secrets.token_bytes(32), bcrypt.gensalt(rounds=12)).decode("ascii")


DUMMY_HASH = _make_dummy_hash()


def waste_time_like_a_real_check() -> None:
    """Spend the same time verifying as a real account would.

    Without this, a login for an address that has no account returns noticeably
    faster than one for an address that does, and the difference is enough to
    enumerate accounts.
    """
    verify_password("not-the-password", DUMMY_HASH)
