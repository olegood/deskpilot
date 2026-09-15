"""Creating accounts and checking credentials.

Like the ticket service, nothing here commits: the caller owns the transaction
(see docs/decisions.md, D-031).
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from deskpilot.auth.lockout import is_locked, record_failure, record_success
from deskpilot.auth.passwords import (
    PasswordError,
    check_password_policy,
    hash_password,
    verify_password,
    waste_time_like_a_real_check,
)
from deskpilot.config import AuthSettings
from deskpilot.db.models import Customer, User, UserRole

logger = logging.getLogger(__name__)

# One message for every way a login can fail. Saying "no such account" or "wrong
# password" tells an attacker which addresses are registered.
BAD_CREDENTIALS = "That email and password do not match an account."


class AuthError(Exception):
    """Raised when an account cannot be created or a login cannot be completed."""


def normalise_email(email: str) -> str:
    """Lower-cased and stripped, so one person cannot end up with two accounts."""
    return email.strip().lower()


async def create_user(
    session: AsyncSession,
    email: str,
    password: str,
    full_name: str,
    role: UserRole = UserRole.CUSTOMER,
    rounds: int = 12,
) -> User:
    """Register an account. Does not commit.

    A customer role is linked to the matching Customer row when one exists, which
    is what later lets a login see that person's orders.
    """
    email = normalise_email(email)
    if not email or "@" not in email:
        raise AuthError(f"{email!r} is not an email address")
    if not full_name.strip():
        raise AuthError("an account needs a name")
    try:
        check_password_policy(password, email)
    except PasswordError as exc:
        raise AuthError(str(exc)) from exc

    if await session.scalar(select(User).where(User.email == email)) is not None:
        raise AuthError(f"an account already exists for {email}")

    customer_id = None
    if role is UserRole.CUSTOMER:
        customer = await session.scalar(select(Customer).where(Customer.email == email))
        customer_id = customer.id if customer else None

    user = User(
        email=email,
        password_hash=hash_password(password, rounds=rounds),
        full_name=full_name.strip(),
        role=role,
        customer_id=customer_id,
    )
    session.add(user)
    await session.flush()
    return user


async def get_user(session: AsyncSession, email: str) -> User | None:
    """Load an account by email, with its customer eagerly loaded."""
    user: User | None = await session.scalar(
        select(User)
        .where(User.email == normalise_email(email))
        .options(selectinload(User.customer))
    )
    return user


async def authenticate(
    session: AsyncSession, email: str, password: str, settings: AuthSettings | None = None
) -> User:
    """Check credentials and return the account, or raise AuthError.

    Every failure takes roughly the same time and gives the same message, so a
    caller cannot learn whether an address is registered, whether it is disabled,
    or whether it is currently locked.

    Mutates the failure counter but does not commit. The caller must commit even
    when this raises, or a failed attempt is never recorded and the lockout never
    happens.
    """
    settings = settings or AuthSettings()
    user = await get_user(session, email)
    if user is None:
        waste_time_like_a_real_check()
        logger.info("login failed: no account for that address")
        raise AuthError(BAD_CREDENTIALS)

    if is_locked(user):
        # Deliberately not "locked until 14:32". Saying so would confirm the
        # account exists, and would let an attacker watch their own lockout tick
        # down. The log says it; the person typing does not hear it.
        waste_time_like_a_real_check()
        logger.info("login refused: account %s is locked", user.id)
        raise AuthError(BAD_CREDENTIALS)

    if not verify_password(password, user.password_hash):
        record_failure(user, settings)
        logger.info("login failed: wrong password for user %s", user.id)
        raise AuthError(BAD_CREDENTIALS)

    if not user.is_active:
        # Still the same message: a disabled account is not something to confirm
        # to whoever is typing at the login form.
        logger.info("login failed: account %s is disabled", user.id)
        raise AuthError(BAD_CREDENTIALS)

    record_success(user)
    return user


def revoke_all_tokens(user: User) -> None:
    """Invalidate every token this user holds. Does not commit."""
    user.token_version += 1


async def set_password(session: AsyncSession, user: User, password: str, rounds: int = 12) -> None:
    """Change a password and invalidate existing sessions. Does not commit."""
    try:
        check_password_policy(password, user.email)
    except PasswordError as exc:
        raise AuthError(str(exc)) from exc
    user.password_hash = hash_password(password, rounds=rounds)
    # A password change that leaves old sessions working is not a password change.
    revoke_all_tokens(user)
