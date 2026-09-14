"""Integration tests for get_customer and list_orders against the real database.

Run with: uv run pytest -m integration (needs `docker compose up -d`).
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deskpilot.config import ToolSettings
from deskpilot.db.models import OrderStatus
from deskpilot.graph.context import AgentContext
from deskpilot.tools.orders import NO_ORDERS, NONE_MATCHING, list_orders
from tests.support import invoke_tool

pytestmark = pytest.mark.integration

NOAH = "noah.kim@example.com"
ANA = "ana.garcia@example.com"
# Seeded with orders, all of them cancelled or delivered; used for status filters.
SOFIA = "sofia.rossi@example.com"


@pytest.fixture
def as_noah(seeded_sessions: async_sessionmaker[AsyncSession]) -> AgentContext:
    return AgentContext(customer_email=NOAH, session_factory=seeded_sessions)


# ── get_customer ────────────────────────────────────────────────────────────


async def test_get_customer_describes_the_acting_customer(as_noah: AgentContext) -> None:
    from deskpilot.tools.customers import get_customer

    message = await invoke_tool(get_customer, as_noah)

    text = str(message.content)
    assert "Noah Kim" in text
    assert "Membership tier: standard" in text
    assert "Orders placed: 2" in text


async def test_get_customer_follows_the_context_not_an_argument(
    seeded_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """The model cannot ask about anybody else: there is no argument to ask with."""
    from deskpilot.tools.customers import get_customer

    as_ana = AgentContext(customer_email=ANA, session_factory=seeded_sessions)

    message = await invoke_tool(get_customer, as_ana)

    text = str(message.content)
    assert "Ana Garc" in text
    assert "Membership tier: gold" in text
    assert "Noah" not in text


# ── list_orders ─────────────────────────────────────────────────────────────


async def test_list_orders_returns_only_this_customers_orders(as_noah: AgentContext) -> None:
    message = await invoke_tool(list_orders, as_noah)

    text = str(message.content)
    assert "ORD-1042" in text
    assert "ORD-1031" in text
    # Ana's orders must not appear.
    assert "ORD-1001" not in text
    assert "ORD-1002" not in text


async def test_list_orders_is_newest_first(as_noah: AgentContext) -> None:
    text = str((await invoke_tool(list_orders, as_noah)).content)

    assert text.index("ORD-1042") < text.index("ORD-1031")


async def test_list_orders_filters_by_status(as_noah: AgentContext) -> None:
    message = await invoke_tool(list_orders, as_noah, status=OrderStatus.SHIPPED)

    text = str(message.content)
    assert "ORD-1042" in text
    assert "ORD-1031" not in text


async def test_list_orders_says_when_a_filter_matches_nothing(as_noah: AgentContext) -> None:
    message = await invoke_tool(list_orders, as_noah, status=OrderStatus.PENDING)

    assert str(message.content) == NONE_MATCHING


async def test_list_orders_says_when_there_are_no_orders_at_all(
    seeded_sessions: async_sessionmaker[AsyncSession],
) -> None:
    stranger = AgentContext(customer_email="nobody@example.com", session_factory=seeded_sessions)

    message = await invoke_tool(list_orders, stranger)

    assert str(message.content) == NO_ORDERS


async def test_list_orders_admits_when_it_has_truncated(
    seeded_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """A model shown a truncated list will otherwise report it as the whole list."""
    cramped = AgentContext(
        customer_email=NOAH,
        session_factory=seeded_sessions,
        tools=ToolSettings(max_orders_listed=1),
    )

    message = await invoke_tool(list_orders, cramped)

    text = str(message.content)
    assert "ORD-1042" in text
    assert "ORD-1031" not in text
    assert "showing the 1 most recent of 2" in text


async def test_list_orders_does_not_mention_truncation_when_complete(
    as_noah: AgentContext,
) -> None:
    assert "showing the" not in str((await invoke_tool(list_orders, as_noah)).content)


async def test_list_orders_totals_match_get_order(as_noah: AgentContext) -> None:
    """The summary line and the detail view must not disagree about money."""
    from deskpilot.tools.orders import get_order

    listed = str((await invoke_tool(list_orders, as_noah)).content)
    detail = str((await invoke_tool(get_order, as_noah, order_number="ORD-1042")).content)

    assert "177.00 USD" in listed
    assert "Total: 177.00 USD" in detail
