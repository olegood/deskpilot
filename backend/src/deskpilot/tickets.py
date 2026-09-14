"""Ticket lifecycle: create a conversation, list them, move them between states.

A ticket row is deliberately thin. The conversation itself lives in LangGraph's
checkpoint tables under `thread_id`, so there is one copy of it, not two.

None of these functions commit. The caller owns the transaction, so one command can
create a ticket, run the agent, and set the resulting status as a single unit. A
service that opened its own transaction would also fail the moment it ran after a
plain SELECT, because that has already started one implicitly.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from deskpilot.db.models import Customer, Ticket, TicketStatus


class TicketError(Exception):
    """Raised when a ticket cannot be created or found."""


async def create_ticket(session: AsyncSession, customer_email: str, subject: str) -> Ticket:
    """Open a new ticket for a customer. Does not commit."""
    subject = subject.strip()
    if not subject:
        raise TicketError("a ticket needs a subject")
    customer = await session.scalar(select(Customer).where(Customer.email == customer_email))
    if customer is None:
        raise TicketError(f"no customer with the email {customer_email!r}")
    ticket = Ticket(
        reference="",  # replaced below, once the row has an id
        thread_id=str(uuid.uuid4()),
        customer_id=customer.id,
        subject=subject,
        status=TicketStatus.OPEN,
    )
    session.add(ticket)
    # flush assigns the primary key without ending the transaction, so the
    # human-readable reference can be derived from it.
    await session.flush()
    ticket.reference = f"TCK-{ticket.id:04d}"
    return ticket


async def get_ticket(session: AsyncSession, reference: str, customer_email: str) -> Ticket:
    """Load one of this customer's tickets.

    Scoped by customer for the same reason get_order is: one answer for "does not
    exist" and "belongs to someone else".
    """
    ticket = await session.scalar(
        select(Ticket)
        .where(
            Ticket.reference == reference.strip().upper(),
            Ticket.customer.has(email=customer_email),
        )
        .options(selectinload(Ticket.customer))
    )
    if ticket is None:
        raise TicketError(f"no ticket {reference!r} was found for this customer")
    return ticket


async def list_tickets(
    session: AsyncSession,
    customer_email: str | None = None,
    limit: int = 50,
) -> list[Ticket]:
    """Recent tickets, newest first. Without an email, every customer's."""
    query = select(Ticket).options(selectinload(Ticket.customer))
    if customer_email is not None:
        query = query.where(Ticket.customer.has(email=customer_email))
    query = query.order_by(Ticket.created_at.desc(), Ticket.id.desc()).limit(limit)
    return list(await session.scalars(query))


def set_status(ticket: Ticket, status: TicketStatus) -> None:
    """Move a ticket to a new state. Does not commit."""
    ticket.status = status
