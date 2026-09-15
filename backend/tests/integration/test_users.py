"""Integration tests for accounts against the real database.

Run with: uv run pytest -m integration (needs `docker compose up -d`).
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deskpilot.auth.lockout import is_locked
from deskpilot.auth.users import (
    BAD_CREDENTIALS,
    AuthError,
    authenticate,
    create_user,
    get_user,
    revoke_all_tokens,
    set_password,
)
from deskpilot.config import AuthSettings
from deskpilot.db.models import UserRole

pytestmark = pytest.mark.integration

NOAH = "noah.kim@example.com"
ANA = "ana.garcia@example.com"
GOOD = "correct horse battery staple"
# 4 rounds instead of 12: the security property is the algorithm, not the cost.
ROUNDS = 4


async def register(
    sessions: async_sessionmaker[AsyncSession],
    email: str = NOAH,
    password: str = GOOD,
    role: UserRole = UserRole.CUSTOMER,
) -> int:
    async with sessions() as session:
        user = await create_user(session, email, password, "Test Person", role, rounds=ROUNDS)
        await session.commit()
        return user.id


async def test_a_customer_account_is_linked_to_its_customer_row(
    seeded_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await register(seeded_sessions)

    async with seeded_sessions() as session:
        user = await get_user(session, NOAH)

    assert user is not None
    assert user.customer is not None
    assert user.customer.email == NOAH


async def test_a_staff_account_is_not_linked_to_a_customer(
    seeded_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """A reviewer logs in and has never bought anything."""
    await register(seeded_sessions, "reviewer@acmegear.example", role=UserRole.REVIEWER)

    async with seeded_sessions() as session:
        user = await get_user(session, "reviewer@acmegear.example")

    assert user is not None
    assert user.customer_id is None
    assert user.role is UserRole.REVIEWER


async def test_email_is_normalised_so_one_person_gets_one_account(
    seeded_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await register(seeded_sessions, "  Noah.Kim@Example.COM  ")

    async with seeded_sessions() as session:
        assert await get_user(session, NOAH) is not None
        with pytest.raises(AuthError, match="already exists"):
            await create_user(session, "NOAH.KIM@example.com", GOOD, "Twin", rounds=ROUNDS)


async def test_the_password_is_not_stored(
    seeded_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await register(seeded_sessions)

    async with seeded_sessions() as session:
        user = await get_user(session, NOAH)

    assert user is not None
    assert GOOD not in user.password_hash
    # repr is overridden precisely so a stray log line cannot carry the hash.
    assert user.password_hash not in repr(user)


async def test_correct_credentials_authenticate(
    seeded_sessions: async_sessionmaker[AsyncSession],
) -> None:
    user_id = await register(seeded_sessions)

    async with seeded_sessions() as session:
        user = await authenticate(session, NOAH, GOOD)

    assert user.id == user_id


async def test_a_wrong_password_is_refused(
    seeded_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await register(seeded_sessions)

    async with seeded_sessions() as session:
        with pytest.raises(AuthError, match=BAD_CREDENTIALS):
            await authenticate(session, NOAH, "not the password at all")


async def test_an_unknown_account_gives_the_same_message_as_a_wrong_password(
    seeded_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Different messages would tell an attacker which addresses are registered."""
    await register(seeded_sessions)

    async with seeded_sessions() as session:
        with pytest.raises(AuthError) as unknown:
            await authenticate(session, "nobody@example.com", GOOD)
        with pytest.raises(AuthError) as wrong:
            await authenticate(session, NOAH, "not the password at all")

    assert str(unknown.value) == str(wrong.value) == BAD_CREDENTIALS


async def test_a_disabled_account_cannot_log_in(
    seeded_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await register(seeded_sessions)

    async with seeded_sessions() as session:
        user = await get_user(session, NOAH)
        assert user is not None
        user.is_active = False
        await session.commit()

    async with seeded_sessions() as session:
        with pytest.raises(AuthError, match=BAD_CREDENTIALS):
            await authenticate(session, NOAH, GOOD)


async def test_changing_a_password_revokes_existing_sessions(
    seeded_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await register(seeded_sessions)

    async with seeded_sessions() as session:
        user = await get_user(session, NOAH)
        assert user is not None
        before = user.token_version
        await set_password(session, user, "a completely different secret", rounds=ROUNDS)
        await session.commit()

    async with seeded_sessions() as session:
        user = await authenticate(session, NOAH, "a completely different secret")

    assert user.token_version == before + 1


async def test_revoking_bumps_the_token_version(
    seeded_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await register(seeded_sessions)

    async with seeded_sessions() as session:
        user = await get_user(session, NOAH)
        assert user is not None
        revoke_all_tokens(user)
        await session.commit()

    async with seeded_sessions() as session:
        refreshed = await get_user(session, NOAH)

    assert refreshed is not None
    assert refreshed.token_version == 2


async def test_a_weak_password_is_refused_at_registration(
    seeded_sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with seeded_sessions() as session:
        with pytest.raises(AuthError, match="at least"):
            await create_user(session, NOAH, "short", "Test Person", rounds=ROUNDS)


async def test_an_address_without_an_at_sign_is_refused(
    seeded_sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with seeded_sessions() as session:
        with pytest.raises(AuthError, match="not an email address"):
            await create_user(session, "noah", GOOD, "Test Person", rounds=ROUNDS)


# ── lockout ─────────────────────────────────────────────────────────────────

LOCKOUT = AuthSettings(
    jwt_secret="test-signing-key",
    bcrypt_rounds=ROUNDS,
    max_failed_logins=3,
    lockout_seconds=60,
)


async def fail_login(sessions: async_sessionmaker[AsyncSession], times: int) -> None:
    for _ in range(times):
        async with sessions() as session:
            with pytest.raises(AuthError):
                await authenticate(session, NOAH, "not the password at all", LOCKOUT)
            # Committed on failure: otherwise the counter never advances.
            await session.commit()


async def test_failed_attempts_are_counted(
    seeded_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await register(seeded_sessions)

    await fail_login(seeded_sessions, 2)

    async with seeded_sessions() as session:
        user = await get_user(session, NOAH)
    assert user is not None
    assert user.failed_logins == 2
    assert user.locked_until is None


async def test_too_many_failures_lock_the_account(
    seeded_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await register(seeded_sessions)

    await fail_login(seeded_sessions, LOCKOUT.max_failed_logins)

    async with seeded_sessions() as session:
        user = await get_user(session, NOAH)
    assert user is not None
    assert is_locked(user)


async def test_a_locked_account_refuses_the_right_password(
    seeded_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """The point of the lockout: guessing correctly afterwards still fails."""
    await register(seeded_sessions)
    await fail_login(seeded_sessions, LOCKOUT.max_failed_logins)

    async with seeded_sessions() as session:
        with pytest.raises(AuthError, match=BAD_CREDENTIALS):
            await authenticate(session, NOAH, GOOD, LOCKOUT)


async def test_a_lockout_says_nothing_about_being_a_lockout(
    seeded_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Saying "locked until 14:32" would confirm the account exists."""
    await register(seeded_sessions)
    await fail_login(seeded_sessions, LOCKOUT.max_failed_logins)

    async with seeded_sessions() as session:
        with pytest.raises(AuthError) as locked:
            await authenticate(session, NOAH, GOOD, LOCKOUT)
        with pytest.raises(AuthError) as unknown:
            await authenticate(session, "nobody@example.com", GOOD, LOCKOUT)

    assert str(locked.value) == str(unknown.value) == BAD_CREDENTIALS


async def test_a_success_before_the_threshold_clears_the_counter(
    seeded_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await register(seeded_sessions)
    await fail_login(seeded_sessions, LOCKOUT.max_failed_logins - 1)

    async with seeded_sessions() as session:
        await authenticate(session, NOAH, GOOD, LOCKOUT)
        await session.commit()

    async with seeded_sessions() as session:
        user = await get_user(session, NOAH)
    assert user is not None
    assert user.failed_logins == 0


async def test_an_expired_lockout_lets_the_right_password_through(
    seeded_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await register(seeded_sessions)
    await fail_login(seeded_sessions, LOCKOUT.max_failed_logins)

    async with seeded_sessions() as session:
        user = await get_user(session, NOAH)
        assert user is not None
        # Wind the clock forward rather than waiting a minute in a test.
        user.locked_until = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()

    async with seeded_sessions() as session:
        assert (await authenticate(session, NOAH, GOOD, LOCKOUT)).email == NOAH


async def test_locking_one_account_does_not_lock_another(
    seeded_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await register(seeded_sessions)
    await register(seeded_sessions, ANA)
    await fail_login(seeded_sessions, LOCKOUT.max_failed_logins)

    async with seeded_sessions() as session:
        assert (await authenticate(session, ANA, GOOD, LOCKOUT)).email == ANA
