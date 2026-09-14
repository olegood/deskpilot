"""Login, refresh rotation, reuse detection, and logout, against the real database.

Run with: uv run pytest -m integration (needs `docker compose up -d`).
"""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deskpilot.auth.sessions import (
    IssuedSession,
    authenticate_access_token,
    log_in,
    log_out,
    refresh,
)
from deskpilot.auth.tokens import TokenError, hash_refresh_token
from deskpilot.auth.users import AuthError, create_user, get_user, revoke_all_tokens
from deskpilot.config import AuthSettings
from deskpilot.db.models import RefreshToken

pytestmark = pytest.mark.integration

NOAH = "noah.kim@example.com"
GOOD = "correct horse battery staple"
ROUNDS = 4
AUTH = AuthSettings(jwt_secret="test-signing-key-not-used-anywhere-real", bcrypt_rounds=ROUNDS)


@pytest.fixture
async def registered(
    seeded_sessions: async_sessionmaker[AsyncSession],
) -> async_sessionmaker[AsyncSession]:
    async with seeded_sessions() as session:
        await create_user(session, NOAH, GOOD, "Noah Kim", rounds=ROUNDS)
        await session.commit()
    return seeded_sessions


async def login(sessions: async_sessionmaker[AsyncSession]) -> IssuedSession:
    async with sessions() as session:
        issued = await log_in(session, NOAH, GOOD, AUTH)
        await session.commit()
        return issued


async def test_logging_in_issues_a_usable_pair(
    registered: async_sessionmaker[AsyncSession],
) -> None:
    issued = await login(registered)

    async with registered() as session:
        user = await authenticate_access_token(session, issued.access_token, AUTH)

    assert user.email == NOAH
    assert issued.refresh_expires_at > issued.access_expires_at


async def test_the_refresh_token_is_stored_only_as_a_digest(
    registered: async_sessionmaker[AsyncSession],
) -> None:
    issued = await login(registered)

    async with registered() as session:
        stored = await session.scalar(select(RefreshToken))

    assert stored is not None
    assert stored.token_hash == hash_refresh_token(issued.refresh_token)
    assert issued.refresh_token not in stored.token_hash


async def test_bad_credentials_issue_nothing(
    registered: async_sessionmaker[AsyncSession],
) -> None:
    async with registered() as session:
        with pytest.raises(AuthError):
            await log_in(session, NOAH, "not the password at all", AUTH)
        await session.rollback()

    async with registered() as session:
        assert await session.scalar(select(RefreshToken)) is None


async def test_refreshing_rotates_both_tokens(
    registered: async_sessionmaker[AsyncSession],
) -> None:
    first = await login(registered)

    async with registered() as session:
        second = await refresh(session, first.refresh_token, AUTH)
        await session.commit()

    assert second.refresh_token != first.refresh_token
    assert second.access_token != first.access_token
    async with registered() as session:
        user = await authenticate_access_token(session, second.access_token, AUTH)
    assert user.email == NOAH


async def test_a_rotated_token_stays_in_the_same_family(
    registered: async_sessionmaker[AsyncSession],
) -> None:
    first = await login(registered)

    async with registered() as session:
        await refresh(session, first.refresh_token, AUTH)
        await session.commit()

    async with registered() as session:
        families = set(await session.scalars(select(RefreshToken.family_id)))

    assert len(families) == 1


async def test_reusing_a_rotated_token_revokes_the_whole_family(
    registered: async_sessionmaker[AsyncSession],
) -> None:
    """The theft case: the thief refreshes, then the owner does, or vice versa.

    Which of the two is the thief is unknowable, so both lose the session.
    """
    first = await login(registered)
    async with registered() as session:
        second = await refresh(session, first.refresh_token, AUTH)
        await session.commit()

    # The old token turns up again: somebody kept a copy.
    async with registered() as session:
        with pytest.raises(TokenError, match="signed out for safety"):
            await refresh(session, first.refresh_token, AUTH)
        await session.commit()

    # The token the honest holder has is dead too.
    async with registered() as session:
        with pytest.raises(TokenError, match="signed out"):
            await refresh(session, second.refresh_token, AUTH)


async def test_a_new_login_starts_a_separate_family(
    registered: async_sessionmaker[AsyncSession],
) -> None:
    """Signing out one device must not sign out the others."""
    laptop = await login(registered)
    phone = await login(registered)

    async with registered() as session:
        await log_out(session, laptop.refresh_token)
        await session.commit()

    async with registered() as session:
        with pytest.raises(TokenError):
            await refresh(session, laptop.refresh_token, AUTH)
    async with registered() as session:
        still_working = await refresh(session, phone.refresh_token, AUTH)

    assert still_working.access_token


async def test_an_unknown_refresh_token_is_refused(
    registered: async_sessionmaker[AsyncSession],
) -> None:
    async with registered() as session:
        with pytest.raises(TokenError, match="not valid"):
            await refresh(session, "a token nobody ever issued", AUTH)


async def test_logging_out_an_unknown_token_is_silent(
    registered: async_sessionmaker[AsyncSession],
) -> None:
    """A logout that reports whether a token existed is an oracle."""
    async with registered() as session:
        await log_out(session, "a token nobody ever issued")


async def test_revoking_an_account_kills_its_access_tokens(
    registered: async_sessionmaker[AsyncSession],
) -> None:
    issued = await login(registered)

    async with registered() as session:
        user = await get_user(session, NOAH)
        assert user is not None
        revoke_all_tokens(user)
        await session.commit()

    async with registered() as session:
        with pytest.raises(TokenError, match="signed out"):
            await authenticate_access_token(session, issued.access_token, AUTH)


async def test_disabling_an_account_kills_its_access_tokens(
    registered: async_sessionmaker[AsyncSession],
) -> None:
    """The signature is still valid; the database says otherwise, and wins."""
    issued = await login(registered)

    async with registered() as session:
        user = await get_user(session, NOAH)
        assert user is not None
        user.is_active = False
        await session.commit()

    async with registered() as session:
        with pytest.raises(TokenError, match="not active"):
            await authenticate_access_token(session, issued.access_token, AUTH)


async def test_a_disabled_account_cannot_refresh_either(
    registered: async_sessionmaker[AsyncSession],
) -> None:
    issued = await login(registered)

    async with registered() as session:
        user = await get_user(session, NOAH)
        assert user is not None
        user.is_active = False
        await session.commit()

    async with registered() as session:
        with pytest.raises(TokenError, match="not active"):
            await refresh(session, issued.refresh_token, AUTH)
