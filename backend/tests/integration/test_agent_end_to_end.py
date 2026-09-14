"""End-to-end agent runs against the real model and the real database.

This is the milestone-1 acceptance test: a customer question goes in, the agent
decides to use a tool, and a grounded answer comes out. It is slower and slightly
nondeterministic, which is why graph logic is covered by tests/graph instead.

Run with: uv run pytest -m integration (needs Ollama and `docker compose up -d`).
"""

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deskpilot.config import Settings
from deskpilot.graph.context import AgentContext
from deskpilot.graph.runner import AgentRun, run_agent

pytestmark = pytest.mark.integration

NOAH = "noah.kim@example.com"
ANA = "ana.garcia@example.com"


async def ask(
    question: str,
    customer_email: str,
    sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> AgentRun:
    context = AgentContext(customer_email=customer_email, session_factory=sessions)
    # A throwaway thread and no checkpointer: each of these is a one-shot run.
    return await run_agent(question, context, str(uuid.uuid4()), settings=settings)


async def test_agent_looks_up_an_order_and_answers_from_the_result(
    seeded_sessions: async_sessionmaker[AsyncSession], agent_settings: Settings
) -> None:
    run = await ask("Hi, where is my order ORD-1042?", NOAH, seeded_sessions, agent_settings)

    assert run.tool_calls == ["get_order"], f"expected one lookup, got: {run.messages!r}"
    answer = run.answer.lower()
    assert "shipped" in answer or "ST-100042".lower() in answer, run.answer
    assert run.steps == 2
    assert run.total_tokens > 0
    assert "<think>" not in run.answer


async def test_agent_does_not_reveal_another_customers_order(
    seeded_sessions: async_sessionmaker[AsyncSession], agent_settings: Settings
) -> None:
    """ORD-1001 is Ana's. Asking as Noah must not leak any of its details."""
    run = await ask("What is the status of ORD-1001?", NOAH, seeded_sessions, agent_settings)

    assert "ST-100001" not in run.answer
    assert "Ridgeline" not in run.answer
    assert "439" not in run.answer  # the order total, in any formatting


async def test_agent_answers_without_tools_when_no_lookup_is_needed(
    seeded_sessions: async_sessionmaker[AsyncSession], agent_settings: Settings
) -> None:
    run = await ask("Hello! Are you a human?", NOAH, seeded_sessions, agent_settings)

    assert run.tool_calls == []
    assert run.steps == 1
    assert run.answer
