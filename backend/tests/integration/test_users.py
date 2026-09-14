"""Integration tests for accounts against the real database.

Run with: uv run pytest -m integration (needs `docker compose up -d`).
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deskpilot.auth.users import (
    BAD_CREDENTIALS,
    AuthError,
    authenticate,
    create_user,
    get_user,
    revoke_all_tokens,
    set_password,
)
from deskpilot.db.models import UserRole

pytestmark = pytest.mark.integration

NOAH = "noah.kim@example.com"
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
