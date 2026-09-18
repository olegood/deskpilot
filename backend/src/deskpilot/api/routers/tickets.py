"""Support tickets over HTTP.

Every endpoint asks the policy engine before it does anything, and the answer is
recorded. That is the same `guard` the tools call, on the same rules, so there is
one definition of who may read a ticket rather than one per entry point.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime

from fastapi import APIRouter, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from deskpilot.api import sse
from deskpilot.api.deps import (
    Agent,
    AppSettings,
    Carrier,
    Checkpointer,
    CurrentPrincipal,
    DbSession,
    Sessions,
)
from deskpilot.authz import resources
from deskpilot.authz.actions import Action
from deskpilot.authz.audit import guard
from deskpilot.authz.principal import Principal
from deskpilot.db.models import Ticket, TicketCategory, TicketStatus
from deskpilot.graph.context import AgentContext
from deskpilot.graph.conversation import load_messages, to_turns
from deskpilot.graph.runner import AgentRun, run_turn, stream_turn
from deskpilot.integrations.shiptrack import ShipTrackClient
from deskpilot.tickets import (
    TicketError,
    create_ticket,
    get_ticket,
    list_tickets,
    set_status,
)

router = APIRouter(prefix="/api/tickets", tags=["tickets"])


class NewTicket(BaseModel):
    subject: str = Field(min_length=1, max_length=200)
    message: str = Field(min_length=1, max_length=5000)


class Reply(BaseModel):
    message: str = Field(min_length=1, max_length=5000)


class TicketSummary(BaseModel):
    reference: str
    subject: str
    status: TicketStatus
    category: TicketCategory | None
    created_at: datetime
    updated_at: datetime


class Message(BaseModel):
    speaker: str
    text: str


class TicketDetail(TicketSummary):
    messages: list[Message]


class Answer(BaseModel):
    """What one turn produced, for the caller that asked for it."""

    reference: str
    answer: str
    status: TicketStatus
    category: TicketCategory | None
    escalated: bool


def summarise(ticket: Ticket) -> TicketSummary:
    return TicketSummary(
        reference=ticket.reference,
        subject=ticket.subject,
        status=ticket.status,
        category=ticket.category,
        created_at=ticket.created_at,
        updated_at=ticket.updated_at,
    )


def own_scope(principal: Principal) -> resources.Ticket:
    """The customer's own tickets, as a resource.

    A principal with no customer record gets an id nothing owns, so the ownership
    rule does not match and the request is denied by default - and recorded. An
    earlier version raised from a dependency instead, which refused correctly and
    left no trace of having done so.
    """
    return resources.Ticket(
        owner_customer_id=principal.customer_id or -1, region=principal.home_region
    )


def as_resource(ticket: Ticket, principal: Principal) -> resources.Ticket:
    """Describe a ticket to the policy.

    The region comes from the principal's own customer record, which is the only
    region a customer's ticket can have. Staff reach tickets through their own
    rule, which reads the region off the ticket's owner instead.
    """
    return resources.Ticket(owner_customer_id=ticket.customer_id, region=principal.home_region)


async def respond(
    agent: Agent,
    sessions: Sessions,
    settings: AppSettings,
    principal: Principal,
    ticket: Ticket,
    message: str,
    # No default. A default here meant every caller silently got None, and the
    # carrier tool reported an outage that was not happening.
    carrier: ShipTrackClient | None,
) -> AgentRun:
    """Run one agent turn for a ticket and record what it decided.

    Synchronous on purpose for now: the caller waits. Streaming the turn as it
    happens is the next step, and it needs the same call underneath.
    """
    context = AgentContext(
        principal=principal,
        session_factory=sessions,
        policy_search=settings.policy_search,
        tools=settings.tools,
        carrier=carrier,
    )
    return await run_turn(agent, message, context, ticket.thread_id)


def record_outcome(ticket: Ticket, run: AgentRun) -> None:
    set_status(ticket, TicketStatus.ESCALATED if run.escalated else TicketStatus.AWAITING_CUSTOMER)
    if run.category is not None:
        ticket.category = run.category


@router.post("", response_model=Answer, status_code=status.HTTP_201_CREATED)
async def open_ticket(
    body: NewTicket,
    principal: CurrentPrincipal,
    db: DbSession,
    sessions: Sessions,
    agent: Agent,
    settings: AppSettings,
    carrier: Carrier,
) -> Answer:
    """Open a ticket and let the agent answer the first message."""
    await guard(
        sessions,
        principal,
        Action.TICKET_CREATE,
        own_scope(principal),
    )
    ticket = await create_ticket(db, principal.email, body.subject)
    run = await respond(agent, sessions, settings, principal, ticket, body.message, carrier)
    record_outcome(ticket, run)
    await db.commit()
    return Answer(
        reference=ticket.reference,
        answer=run.answer,
        status=ticket.status,
        category=ticket.category,
        escalated=run.escalated,
    )


@router.post("/stream")
async def open_ticket_streaming(
    body: NewTicket,
    principal: CurrentPrincipal,
    db: DbSession,
    sessions: Sessions,
    agent: Agent,
    settings: AppSettings,
    carrier: Carrier,
) -> StreamingResponse:
    """Open a ticket and stream the agent's first answer as it is written."""
    # Everything that can refuse happens here, before a single byte goes out. Once
    # the 200 and the headers are sent, a refusal can only be an event inside a
    # stream the client already accepted.
    await guard(sessions, principal, Action.TICKET_CREATE, own_scope(principal))
    ticket = await create_ticket(db, principal.email, body.subject)
    await db.commit()
    reference = ticket.reference
    thread_id = ticket.thread_id
    return sse.stream(
        sse.guarded(
            turn_events(
                agent, sessions, settings, principal, thread_id, body.message, reference, carrier
            )
        )
    )


@router.post("/{reference}/replies/stream")
async def reply_streaming(
    reference: str,
    body: Reply,
    principal: CurrentPrincipal,
    db: DbSession,
    sessions: Sessions,
    agent: Agent,
    settings: AppSettings,
    carrier: Carrier,
) -> StreamingResponse:
    """Add a message and stream the answer."""
    ticket = await get_ticket(db, reference, principal.email)
    await guard(sessions, principal, Action.TICKET_REPLY, as_resource(ticket, principal))
    if ticket.status is TicketStatus.RESOLVED:
        raise TicketError(f"{ticket.reference} is resolved; open a new ticket instead")
    return sse.stream(
        sse.guarded(
            turn_events(
                agent,
                sessions,
                settings,
                principal,
                ticket.thread_id,
                body.message,
                ticket.reference,
                carrier,
            )
        )
    )


async def turn_events(
    agent: Agent,
    sessions: Sessions,
    settings: AppSettings,
    principal: Principal,
    thread_id: str,
    message: str,
    reference: str,
    carrier: ShipTrackClient | None,
) -> AsyncIterator[str]:
    """One agent turn, as a sequence of server-sent events.

    The ticket row is updated at the end, in its own session. The request's session
    is long gone by the time this runs: FastAPI closes a dependency's session when
    the handler returns, and a streaming handler returns immediately.
    """
    yield sse.event("ticket", {"reference": reference})
    context = AgentContext(
        principal=principal,
        session_factory=sessions,
        policy_search=settings.policy_search,
        tools=settings.tools,
        carrier=carrier,
    )
    final: dict[str, object] = {}
    async for produced in stream_turn(agent, message, context, thread_id):
        if produced.kind == "answer":
            final = dict(produced.data)
        yield sse.event(produced.kind, produced.data)

    async with sessions() as session:
        ticket = await get_ticket(session, reference, principal.email)
        set_status(
            ticket,
            TicketStatus.ESCALATED if final.get("escalated") else TicketStatus.AWAITING_CUSTOMER,
        )
        category = final.get("category")
        if isinstance(category, str):
            ticket.category = TicketCategory(category)
        await session.commit()
        yield sse.event("done", {"reference": reference, "status": ticket.status.value})


@router.get("", response_model=list[TicketSummary])
async def my_tickets(
    principal: CurrentPrincipal, db: DbSession, sessions: Sessions
) -> list[TicketSummary]:
    """The tickets belonging to the signed-in customer."""
    # Scope, not row by row: may this principal read the tickets of the customer
    # they are. The query is then filtered by that customer (D-090).
    await guard(
        sessions,
        principal,
        Action.TICKET_VIEW,
        own_scope(principal),
    )
    return [summarise(ticket) for ticket in await list_tickets(db, principal.email)]


@router.get("/{reference}", response_model=TicketDetail)
async def read_ticket(
    reference: str,
    principal: CurrentPrincipal,
    db: DbSession,
    sessions: Sessions,
    checkpointer: Checkpointer,
) -> TicketDetail:
    """One ticket and its conversation."""
    ticket = await get_ticket(db, reference, principal.email)
    await guard(sessions, principal, Action.TICKET_VIEW, as_resource(ticket, principal))
    messages = await load_messages(checkpointer, ticket.thread_id)
    # Tool calls are the agent's working, not the conversation. A customer has no
    # use for them, and they carry raw tool output that nothing has sanitised.
    return TicketDetail(
        **summarise(ticket).model_dump(),
        messages=[Message(speaker=turn.speaker, text=turn.text) for turn in to_turns(messages)],
    )


@router.post("/{reference}/replies", response_model=Answer)
async def reply_to_ticket(
    reference: str,
    body: Reply,
    principal: CurrentPrincipal,
    db: DbSession,
    sessions: Sessions,
    agent: Agent,
    settings: AppSettings,
    carrier: Carrier,
) -> Answer:
    """Add a message to a ticket and let the agent respond."""
    ticket = await get_ticket(db, reference, principal.email)
    await guard(sessions, principal, Action.TICKET_REPLY, as_resource(ticket, principal))
    if ticket.status is TicketStatus.RESOLVED:
        raise TicketError(f"{ticket.reference} is resolved; open a new ticket instead")

    run = await respond(agent, sessions, settings, principal, ticket, body.message, carrier)
    record_outcome(ticket, run)
    await db.commit()
    return Answer(
        reference=ticket.reference,
        answer=run.answer,
        status=ticket.status,
        category=ticket.category,
        escalated=run.escalated,
    )
