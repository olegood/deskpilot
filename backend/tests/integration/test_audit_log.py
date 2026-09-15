"""The audit log against the real database.

Run with: uv run pytest -m integration (needs `docker compose up -d`).
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deskpilot.auth.users import create_user
from deskpilot.authz import resources
from deskpilot.authz.actions import Action, AuditEvent
from deskpilot.authz.audit import guard, recent, record_event
from deskpilot.authz.engine import Forbidden
from deskpilot.authz.principal import Principal
from deskpilot.db.models import AuditKind, Region, UserRole

pytestmark = pytest.mark.integration

NOAH = "noah.kim@example.com"
GOOD = "correct horse battery staple"
ROUNDS = 4


@pytest.fixture
async def reviewer(seeded_sessions: async_sessionmaker[AsyncSession]) -> Principal:
    async with seeded_sessions() as session:
        user = await create_user(
            session, "lena@acmegear.example", GOOD, "Lena Fox", UserRole.REVIEWER, rounds=ROUNDS
        )
        user.regions = [Region.EU.value]
        user.approval_limit_cents = 50_000
        await session.commit()
        return Principal.from_user(user)


def proposal(amount_cents: int, region: Region = Region.EU) -> resources.Proposal:
    return resources.Proposal(ticket_owner_customer_id=99, region=region, amount_cents=amount_cents)


async def test_an_allowed_approval_is_recorded(
    seeded_sessions: async_sessionmaker[AsyncSession], reviewer: Principal
) -> None:
    await guard(seeded_sessions, reviewer, Action.REFUND_APPROVE, proposal(40_000))

    async with seeded_sessions() as session:
        entries = await recent(session)

    assert len(entries) == 1
    assert entries[0].event == "refund.approve"
    assert entries[0].allowed
    assert entries[0].actor_user_id == reviewer.user_id
    assert entries[0].kind is AuditKind.AUTHZ


async def test_a_refusal_is_recorded_with_the_rule_and_the_real_reason(
    seeded_sessions: async_sessionmaker[AsyncSession], reviewer: Principal
) -> None:
    """The caller gets a flat refusal; the log gets the detail."""
    with pytest.raises(Forbidden):
        await guard(seeded_sessions, reviewer, Action.REFUND_APPROVE, proposal(90_000))

    async with seeded_sessions() as session:
        entries = await recent(session)

    assert not entries[0].allowed
    assert entries[0].rule == "a_reviewer_approves_within_their_limit"
    assert "50000" in entries[0].reason


async def test_a_refusal_survives_the_caller_rolling_back(
    seeded_sessions: async_sessionmaker[AsyncSession], reviewer: Principal
) -> None:
    """The whole point: the record must not roll back with the denied action."""
    async with seeded_sessions() as session:
        with pytest.raises(Forbidden):
            await guard(seeded_sessions, reviewer, Action.REFUND_APPROVE, proposal(90_000))
        await session.rollback()

    async with seeded_sessions() as session:
        assert len(await recent(session)) == 1


async def test_a_routine_read_is_not_recorded(
    seeded_sessions: async_sessionmaker[AsyncSession], reviewer: Principal
) -> None:
    await guard(seeded_sessions, reviewer, Action.POLICY_SEARCH, resources.PolicyDocuments())

    async with seeded_sessions() as session:
        assert await recent(session) == []


async def test_auth_events_are_recorded_alongside_decisions(
    seeded_sessions: async_sessionmaker[AsyncSession], reviewer: Principal
) -> None:
    await record_event(
        seeded_sessions,
        AuditEvent.LOGIN_FAILED,
        "login failed for somebody@example.com",
        allowed=False,
    )
    await guard(seeded_sessions, reviewer, Action.REFUND_APPROVE, proposal(1_000))

    async with seeded_sessions() as session:
        entries = await recent(session)

    assert {entry.kind for entry in entries} == {AuditKind.AUTH, AuditKind.AUTHZ}


async def test_an_unidentified_actor_is_allowed(
    seeded_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """A login for an address with no account has nobody to attribute."""
    await record_event(
        seeded_sessions, AuditEvent.LOGIN_FAILED, "no account for that address", allowed=False
    )

    async with seeded_sessions() as session:
        entries = await recent(session)

    assert entries[0].actor_user_id is None


async def test_entries_come_back_newest_first(
    seeded_sessions: async_sessionmaker[AsyncSession], reviewer: Principal
) -> None:
    for amount in (1_000, 2_000, 3_000):
        await guard(seeded_sessions, reviewer, Action.REFUND_APPROVE, proposal(amount))

    async with seeded_sessions() as session:
        entries = await recent(session)

    assert [entry.id for entry in entries] == sorted((entry.id for entry in entries), reverse=True)


async def test_the_log_can_be_filtered(
    seeded_sessions: async_sessionmaker[AsyncSession], reviewer: Principal
) -> None:
    await guard(seeded_sessions, reviewer, Action.REFUND_APPROVE, proposal(1_000))
    with pytest.raises(Forbidden):
        await guard(seeded_sessions, reviewer, Action.REFUND_APPROVE, proposal(90_000))

    async with seeded_sessions() as session:
        assert len(await recent(session, denied_only=True)) == 1
        assert len(await recent(session, actor_user_id=reviewer.user_id)) == 2
        assert await recent(session, actor_user_id=reviewer.user_id + 999) == []


async def test_the_recorded_reason_is_not_what_the_caller_was_told(
    seeded_sessions: async_sessionmaker[AsyncSession], reviewer: Principal
) -> None:
    with pytest.raises(Forbidden) as caught:
        await guard(seeded_sessions, reviewer, Action.REFUND_APPROVE, proposal(90_000))

    async with seeded_sessions() as session:
        entries = await recent(session)

    assert str(caught.value) == "You are not allowed to do that."
    assert entries[0].reason != str(caught.value)
