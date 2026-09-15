"""Integration tests for the get_order tool against the real database.

Run with: uv run pytest -m integration (needs `docker compose up -d`).
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deskpilot.authz.audit import recent
from deskpilot.graph.context import AgentContext
from deskpilot.tools.orders import NOT_FOUND, get_order
from tests.support import invoke_tool, principal_for

pytestmark = pytest.mark.integration

NOAH = "noah.kim@example.com"
ANA = "ana.garcia@example.com"


@pytest.fixture
async def context(seeded_sessions: async_sessionmaker[AsyncSession]) -> AgentContext:
    return AgentContext(
        principal=await principal_for(seeded_sessions, NOAH), session_factory=seeded_sessions
    )


async def test_returns_the_customers_own_order(context: AgentContext) -> None:
    message = await invoke_tool(get_order, context, order_number="ORD-1042")

    assert message.status == "success"
    text = str(message.content)
    assert "Order ORD-1042" in text
    assert "Status: shipped" in text
    assert "ST-100042" in text
    assert "Total: 177.00 USD" in text


async def test_order_number_is_case_and_space_insensitive(context: AgentContext) -> None:
    message = await invoke_tool(get_order, context, order_number="  ord-1042 ")

    assert "Order ORD-1042" in str(message.content)


async def test_another_customers_order_is_not_returned(context: AgentContext) -> None:
    """ORD-1001 exists, but it belongs to Ana, not Noah."""
    message = await invoke_tool(get_order, context, order_number="ORD-1001")

    assert str(message.content) == NOT_FOUND


async def test_unknown_order_looks_the_same_as_someone_elses(context: AgentContext) -> None:
    """Identical answers, so the tool cannot be used to probe which numbers exist."""
    missing = await invoke_tool(get_order, context, order_number="ORD-9999")
    other = await invoke_tool(get_order, context, order_number="ORD-1001")

    assert str(missing.content) == str(other.content) == NOT_FOUND


async def test_the_same_order_is_visible_to_its_owner(context: AgentContext) -> None:
    as_ana = AgentContext(
        principal=await principal_for(context.session_factory, ANA),
        session_factory=context.session_factory,
    )

    message = await invoke_tool(get_order, as_ana, order_number="ORD-1001")

    assert "Order ORD-1001" in str(message.content)


async def test_customer_notes_never_reach_the_model(context: AgentContext) -> None:
    """ORD-1088 has notes; they must not appear in the tool output."""
    as_aisha = AgentContext(
        principal=await principal_for(context.session_factory, "aisha.rahman@example.com"),
        session_factory=context.session_factory,
    )

    message = await invoke_tool(get_order, as_aisha, order_number="ORD-1088")

    assert "Order ORD-1088" in str(message.content)
    assert "back door" not in str(message.content)


# ── the policy is now what refuses, and the attempt is recorded ──────────────


async def test_a_cross_customer_lookup_is_recorded_as_a_denial(
    context: AgentContext,
) -> None:
    """The customer sees "not found"; the log says what really happened.

    Before this step the ownership filter was in the query, so a cross-customer
    attempt looked exactly like a typo and left no trace. Now the row is loaded,
    the policy judges it, and the refusal is evidence.
    """
    await invoke_tool(get_order, context, order_number="ORD-1001")

    async with context.session_factory() as session:
        entries = await recent(session, denied_only=True)

    assert entries
    assert entries[0].event == "order.view"
    assert entries[0].rule == "default"
    assert "ORD" not in entries[0].reason


async def test_a_typo_is_not_recorded_as_a_denial(context: AgentContext) -> None:
    """An order that does not exist never reaches the policy, so nothing is logged."""
    await invoke_tool(get_order, context, order_number="ORD-9999")

    async with context.session_factory() as session:
        assert await recent(session, denied_only=True) == []


async def test_a_successful_lookup_is_not_recorded(context: AgentContext) -> None:
    """Routine reads would bury the entries somebody came looking for."""
    await invoke_tool(get_order, context, order_number="ORD-1042")

    async with context.session_factory() as session:
        assert await recent(session) == []
