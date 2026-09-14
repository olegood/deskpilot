"""Integration tests for tickets and for checkpoint persistence.

Run with: uv run pytest -m integration (needs `docker compose up -d`).
"""

import pytest
from langchain_core.messages import HumanMessage
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deskpilot.config import DatabaseSettings
from deskpilot.db.checkpointer import open_checkpointer
from deskpilot.db.models import TicketStatus
from deskpilot.graph.agent import build_agent_graph
from deskpilot.graph.context import AgentContext
from deskpilot.graph.runner import run_turn
from deskpilot.tickets import TicketError, create_ticket, get_ticket, list_tickets, set_status
from tests.support import ai_text, scripted

pytestmark = pytest.mark.integration

NOAH = "noah.kim@example.com"
ANA = "ana.garcia@example.com"


async def test_creating_a_ticket_assigns_a_reference_and_a_thread(
    seeded_sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with seeded_sessions() as session:
        ticket = await create_ticket(session, NOAH, "Parcel never arrived")
        await session.commit()

    assert ticket.reference.startswith("TCK-")
    assert ticket.status is TicketStatus.OPEN
    assert ticket.thread_id
    assert ticket.thread_id != ticket.reference


async def test_references_are_unique_and_sequential(
    seeded_sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with seeded_sessions() as session:
        first = await create_ticket(session, NOAH, "One")
        second = await create_ticket(session, ANA, "Two")
        await session.commit()

    assert first.reference != second.reference
    assert int(second.reference.removeprefix("TCK-")) > int(first.reference.removeprefix("TCK-"))


async def test_a_ticket_needs_a_subject(
    seeded_sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with seeded_sessions() as session:
        with pytest.raises(TicketError, match="subject"):
            await create_ticket(session, NOAH, "   ")


async def test_unknown_customer_is_rejected(
    seeded_sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with seeded_sessions() as session:
        with pytest.raises(TicketError, match="no customer"):
            await create_ticket(session, "nobody@example.com", "Hello")


async def test_a_ticket_is_not_visible_to_another_customer(
    seeded_sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with seeded_sessions() as session:
        ticket = await create_ticket(session, NOAH, "Parcel never arrived")
        await session.commit()

    async with seeded_sessions() as session:
        assert (await get_ticket(session, ticket.reference, NOAH)).id == ticket.id
        with pytest.raises(TicketError, match="was found for this customer"):
            await get_ticket(session, ticket.reference, ANA)


async def test_listing_can_be_scoped_to_one_customer(
    seeded_sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with seeded_sessions() as session:
        await create_ticket(session, NOAH, "Noah's problem")
        await create_ticket(session, ANA, "Ana's problem")
        await session.commit()

    async with seeded_sessions() as session:
        assert len(await list_tickets(session)) == 2
        for_noah = await list_tickets(session, NOAH)

    assert [t.subject for t in for_noah] == ["Noah's problem"]


async def test_status_changes_are_persisted(
    seeded_sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with seeded_sessions() as session:
        ticket = await create_ticket(session, NOAH, "Parcel never arrived")
        await session.commit()

    async with seeded_sessions() as session:
        loaded = await get_ticket(session, ticket.reference, NOAH)
        set_status(loaded, TicketStatus.ESCALATED)
        await session.commit()

    async with seeded_sessions() as session:
        assert (await get_ticket(session, ticket.reference, NOAH)).status is TicketStatus.ESCALATED


async def test_a_conversation_survives_a_fresh_checkpointer(
    seeded_sessions: async_sessionmaker[AsyncSession], test_database: DatabaseSettings
) -> None:
    """The point of checkpointing: a later process picks the thread up unchanged.

    Each `async with open_checkpointer(...)` block is a separate connection pool and
    a separate graph, which is as close as a test gets to restarting the service.
    """
    async with seeded_sessions() as session:
        ticket = await create_ticket(session, NOAH, "Parcel never arrived")
        await session.commit()
    context = AgentContext(customer_email=NOAH, session_factory=seeded_sessions)

    async with open_checkpointer(test_database) as checkpointer:
        await checkpointer.setup()
        graph = build_agent_graph(
            scripted([ai_text("First answer.")]), [], 6, checkpointer=checkpointer
        )
        first = await run_turn(graph, "First question.", context, ticket.thread_id)

    async with open_checkpointer(test_database) as checkpointer:
        graph = build_agent_graph(
            scripted([ai_text("Second answer.")]), [], 6, checkpointer=checkpointer
        )
        second = await run_turn(graph, "Second question.", context, ticket.thread_id)

    assert first.answer == "First answer."
    assert second.answer == "Second answer."
    # The whole conversation came back, not just this turn.
    assert [m.text for m in second.messages] == [
        "First question.",
        "First answer.",
        "Second question.",
        "Second answer.",
    ]
    assert isinstance(second.messages[0], HumanMessage)
    # Tokens accumulated across both processes.
    assert second.input_tokens == 20


async def test_threads_do_not_leak_into_each_other(
    seeded_sessions: async_sessionmaker[AsyncSession], test_database: DatabaseSettings
) -> None:
    async with seeded_sessions() as session:
        one = await create_ticket(session, NOAH, "First problem")
        two = await create_ticket(session, NOAH, "Second problem")
        await session.commit()
    context = AgentContext(customer_email=NOAH, session_factory=seeded_sessions)

    async with open_checkpointer(test_database) as checkpointer:
        await checkpointer.setup()
        graph = build_agent_graph(scripted([ai_text("Answer.")]), [], 6, checkpointer=checkpointer)
        await run_turn(graph, "About the first.", context, one.thread_id)
        second = await run_turn(graph, "About the second.", context, two.thread_id)

    assert [m.text for m in second.messages] == ["About the second.", "Answer."]
