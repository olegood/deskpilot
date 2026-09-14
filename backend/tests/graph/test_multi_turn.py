"""Multi-turn behaviour: what carries across turns and what resets.

An in-memory checkpointer stands in for PostgreSQL here. Persistence across
processes is covered by the integration tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver

from deskpilot.graph.agent import build_agent_graph
from deskpilot.graph.context import AgentContext
from deskpilot.graph.runner import run_turn
from deskpilot.graph.state import AgentState
from tests.support import ai_text, ai_with_tool_calls, scripted, tool_call

THREAD = "ticket-thread-1"
# run_turn needs an AgentContext, but no tool here touches the database.
SESSIONS: Any = None


@dataclass(frozen=True)
class FakeContext:
    customer_email: str


@tool
async def echo_order(order_number: str) -> str:
    """Report an order's status."""
    return f"{order_number}: shipped"


def turn(message: str) -> AgentState:
    return {
        "messages": [HumanMessage(message)],
        "steps": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "escalated": False,
    }


async def test_conversation_continues_across_turns() -> None:
    model = scripted([ai_text("First answer."), ai_text("Second answer.")])
    graph = build_agent_graph(model, [echo_order], max_steps=6, checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": THREAD}}
    context = FakeContext(customer_email="noah.kim@example.com")

    await graph.ainvoke(turn("First question."), config=config, context=context)
    result = await graph.ainvoke(turn("Second question."), config=config, context=context)

    texts = [m.text for m in result["messages"]]
    assert texts == ["First question.", "First answer.", "Second question.", "Second answer."]


async def test_the_model_sees_the_earlier_conversation() -> None:
    model = scripted([ai_text("First answer."), ai_text("Second answer.")])
    graph = build_agent_graph(model, [echo_order], max_steps=6, checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": THREAD}}
    context = FakeContext(customer_email="noah.kim@example.com")

    await graph.ainvoke(turn("First question."), config=config, context=context)
    await graph.ainvoke(turn("Second question."), config=config, context=context)

    # The second call includes the system prompt plus all four earlier messages.
    second_call = [m.text for m in model.calls[1][1:]]
    assert second_call == ["First question.", "First answer.", "Second question."]


async def test_each_turn_gets_a_fresh_step_budget() -> None:
    """steps must reset, or turn two would start already over budget."""
    model = scripted([ai_with_tool_calls(tool_call("echo_order", order_number="ORD-1042"))])
    graph = build_agent_graph(model, [echo_order], max_steps=2, checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": THREAD}}
    context = FakeContext(customer_email="noah.kim@example.com")

    first = await graph.ainvoke(turn("One."), config=config, context=context)
    second = await graph.ainvoke(turn("Two."), config=config, context=context)

    assert first["steps"] == 2
    assert second["steps"] == 2


async def test_token_counts_accumulate_over_the_whole_ticket() -> None:
    model = scripted([ai_text("First answer."), ai_text("Second answer.")])
    graph = build_agent_graph(model, [echo_order], max_steps=6, checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": THREAD}}
    context = FakeContext(customer_email="noah.kim@example.com")

    first = await graph.ainvoke(turn("One."), config=config, context=context)
    second = await graph.ainvoke(turn("Two."), config=config, context=context)

    # 10 in and 5 out per model call, one call per turn.
    assert (first["input_tokens"], first["output_tokens"]) == (10, 5)
    assert (second["input_tokens"], second["output_tokens"]) == (20, 10)


async def test_run_turn_reports_this_turn_only() -> None:
    """Token counts cover the ticket; tool calls and the answer cover this turn."""
    model = scripted(
        [
            ai_text("First answer."),
            ai_with_tool_calls(tool_call("echo_order", order_number="ORD-1042")),
            ai_text("Second answer."),
        ]
    )
    graph = build_agent_graph(model, [echo_order], max_steps=6, checkpointer=InMemorySaver())
    context = AgentContext(customer_email="noah.kim@example.com", session_factory=SESSIONS)

    first = await run_turn(graph, "One.", context, THREAD)
    second = await run_turn(graph, "Two.", context, THREAD)

    assert first.tool_calls == []
    assert first.answer == "First answer."
    # The second turn used a tool; the first turn's calls are not counted again.
    assert second.tool_calls == ["echo_order"]
    assert second.answer == "Second answer."
    assert len(second.turn_messages) == 4
    assert len(second.messages) == 6
    # Tokens are the ticket total: three model calls at 10 in and 5 out.
    assert (second.input_tokens, second.output_tokens) == (30, 15)


async def test_escalation_is_reported_by_run_turn() -> None:
    model = scripted([ai_with_tool_calls(tool_call("echo_order", order_number="ORD-1042"))])
    graph = build_agent_graph(model, [echo_order], max_steps=2, checkpointer=InMemorySaver())
    context = AgentContext(customer_email="noah.kim@example.com", session_factory=SESSIONS)

    result = await run_turn(graph, "One.", context, THREAD)

    assert result.escalated is True
    assert result.steps == 2
