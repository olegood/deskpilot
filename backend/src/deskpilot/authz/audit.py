"""Recording what happened.

An audit entry is written in its own transaction, separate from whatever the caller
is doing. That is the whole design: a denied action rolls back, and the record of
the denial must not roll back with it. The same mistake in reverse - committing only
on success - is what would have quietly disabled the login lockout (D-073).

Nothing here raises. A logging failure must not turn a working request into a broken
one, so it is logged and swallowed. The trade is deliberate and stated: this is an
accountability record, not a ledger that has to balance.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deskpilot.authz.actions import ALWAYS_AUDITED, Action, AuditEvent
from deskpilot.authz.engine import Decision
from deskpilot.authz.principal import Principal
from deskpilot.authz.resources import Resource
from deskpilot.db.models import AuditEntry, AuditKind

logger = logging.getLogger(__name__)

# Reason text is truncated rather than rejected: a long reason is still worth
# keeping, and a failed insert would lose the entry entirely.
MAX_REASON = 500
MAX_RESOURCE = 200


def describe(resource: Resource) -> str:
    """A short, stable description of what was acted on.

    The class name plus its attributes, not a database id: the row may be deleted,
    and the entry has to stay readable afterwards.
    """
    fields = ", ".join(f"{name}={value!r}" for name, value in vars(resource).items())
    return f"{type(resource).__name__}({fields})"[:MAX_RESOURCE]


def should_record(action: Action, decision: Decision) -> bool:
    """Every denial, and the allows that are worth knowing about.

    A customer reading their own order happens on every turn of every conversation.
    Recording it buries the entries somebody would actually want to find.
    """
    return not decision.allowed or action in ALWAYS_AUDITED


async def record_decision(
    sessions: async_sessionmaker[AsyncSession],
    principal: Principal,
    action: Action,
    resource: Resource,
    decision: Decision,
) -> None:
    """Write one authorization decision, if it is worth writing."""
    if not should_record(action, decision):
        return
    await _write(
        sessions,
        AuditEntry(
            kind=AuditKind.AUTHZ,
            event=action.value,
            actor_user_id=principal.user_id,
            resource=describe(resource),
            allowed=decision.allowed,
            rule=decision.policy,
            reason=decision.reason[:MAX_REASON],
        ),
    )


async def record_event(
    sessions: async_sessionmaker[AsyncSession],
    event: AuditEvent,
    reason: str,
    actor_user_id: int | None = None,
    allowed: bool = True,
    resource: str | None = None,
) -> None:
    """Write something that is not an authorization decision.

    Logins, lockouts, token reuse. `actor_user_id` is None when nobody was
    identified, such as a login for an address with no account.
    """
    await _write(
        sessions,
        AuditEntry(
            kind=AuditKind.AUTH,
            event=event.value,
            actor_user_id=actor_user_id,
            resource=resource[:MAX_RESOURCE] if resource else None,
            allowed=allowed,
            rule=None,
            reason=reason[:MAX_REASON],
        ),
    )


async def _write(sessions: async_sessionmaker[AsyncSession], entry: AuditEntry) -> None:
    try:
        async with sessions() as session:
            session.add(entry)
            await session.commit()
    except Exception:
        # Deliberately swallowed. See the module docstring.
        logger.exception("could not write an audit entry for %s", entry.event)


async def recent(
    session: AsyncSession,
    limit: int = 50,
    actor_user_id: int | None = None,
    denied_only: bool = False,
) -> list[AuditEntry]:
    """Read the log back, newest first."""
    query = select(AuditEntry)
    if actor_user_id is not None:
        query = query.where(AuditEntry.actor_user_id == actor_user_id)
    if denied_only:
        query = query.where(AuditEntry.allowed.is_(False))
    query = query.order_by(AuditEntry.at.desc(), AuditEntry.id.desc()).limit(limit)
    return list(await session.scalars(query))


async def guard(
    sessions: async_sessionmaker[AsyncSession],
    principal: Principal,
    action: Action,
    resource: Resource,
) -> Decision:
    """Decide, record, and raise Forbidden if the answer is no.

    The one call the rest of the application makes. `decide` stays pure and knows
    nothing about a database; this is the layer that remembers.
    """
    from deskpilot.authz.engine import Forbidden, decide

    decision = decide(principal, action, resource)
    await record_decision(sessions, principal, action, resource, decision)
    if not decision.allowed:
        raise Forbidden(decision)
    return decision
