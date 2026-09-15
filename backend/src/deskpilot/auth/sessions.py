"""Logging in, refreshing, and logging out.

A login issues two things: a short access token that proves identity for minutes,
and a long refresh token that buys new access tokens. Refresh tokens rotate, so a
stolen one is only useful until the real owner next refreshes - at which point the
theft is detected and the whole family is revoked.

Nothing here commits. The caller owns the transaction.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from deskpilot.auth.tokens import (
    TokenError,
    check_version,
    decode_access_token,
    hash_refresh_token,
    issue_access_token,
    new_family_id,
    new_refresh_token,
    now,
)
from deskpilot.auth.users import authenticate
from deskpilot.config import AuthSettings
from deskpilot.db.models import RefreshToken, User

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IssuedSession:
    """What a login or a refresh hands back."""

    access_token: str
    refresh_token: str
    access_expires_at: datetime
    refresh_expires_at: datetime
    user_id: int
    email: str


async def log_in(
    session: AsyncSession, email: str, password: str, settings: AuthSettings
) -> IssuedSession:
    """Check credentials and start a new token family. Does not commit."""
    user = await authenticate(session, email, password, settings)
    return await _issue(session, user, new_family_id(), settings)


async def refresh(session: AsyncSession, token: str, settings: AuthSettings) -> IssuedSession:
    """Exchange a refresh token for a new pair. Does not commit.

    The old token is retired in the same transaction as the new one is issued, so
    two concurrent refreshes cannot both succeed.
    """
    stored = await session.scalar(
        select(RefreshToken)
        .where(RefreshToken.token_hash == hash_refresh_token(token))
        .options(selectinload(RefreshToken.user))
    )
    if stored is None:
        # Either invented, or from a family that has since been deleted.
        raise TokenError("this session is not valid")

    if stored.used_at is not None:
        # The real owner already rotated this one, so somebody else is holding a
        # copy. Which of the two is the thief is unknowable, so both lose: every
        # token in the family is revoked and the user logs in again.
        logger.warning(
            "refresh token reuse detected for user %s; revoking family %s",
            stored.user_id,
            stored.family_id,
        )
        await revoke_family(session, stored.family_id)
        raise TokenError("this session has been signed out for safety; please log in again")

    if stored.revoked_at is not None:
        raise TokenError("this session has been signed out")
    if stored.expires_at <= now():
        raise TokenError("this session has expired")

    user = stored.user
    if not user.is_active:
        raise TokenError("this account is not active")

    stored.used_at = now()
    return await _issue(session, user, stored.family_id, settings)


async def log_out(session: AsyncSession, token: str) -> None:
    """Revoke the family a refresh token belongs to. Does not commit.

    Silent when the token is unknown: a logout that reports whether a token existed
    is an oracle, and a failed logout helps nobody.
    """
    stored = await session.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(token))
    )
    if stored is not None:
        await revoke_family(session, stored.family_id)


async def revoke_family(session: AsyncSession, family_id: str) -> None:
    """Revoke every token descended from one login. Does not commit."""
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )


async def authenticate_access_token(
    session: AsyncSession, token: str, settings: AuthSettings
) -> User:
    """Verify an access token and load the account it belongs to.

    The database is consulted even though the signature already proved the token is
    ours, because a signature cannot know that the account was disabled or signed
    out a minute ago.
    """
    claims = decode_access_token(token, settings)
    user = await session.scalar(
        select(User).where(User.id == claims.user_id).options(selectinload(User.customer))
    )
    if user is None:
        raise TokenError("this session is not valid")
    check_version(claims, user)
    return user


async def _issue(
    session: AsyncSession, user: User, family_id: str, settings: AuthSettings
) -> IssuedSession:
    """Mint a pair and record the refresh token's digest."""
    access_token = issue_access_token(user, settings)
    refresh_token = new_refresh_token()
    issued_at = now()
    expires_at = issued_at + timedelta(days=settings.refresh_token_days)

    session.add(
        RefreshToken(
            user_id=user.id,
            token_hash=hash_refresh_token(refresh_token),
            family_id=family_id,
            expires_at=expires_at,
        )
    )
    await session.flush()

    return IssuedSession(
        access_token=access_token,
        refresh_token=refresh_token,
        access_expires_at=issued_at + timedelta(minutes=settings.access_token_minutes),
        refresh_expires_at=expires_at,
        user_id=user.id,
        email=user.email,
    )
