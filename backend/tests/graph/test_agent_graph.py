"""Graph tests: the ReAct loop, routing, and error handling, with a scripted model.

These run in milliseconds and are fully deterministic. No LLM and no database.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool, tool
from langgraph.prebuilt import ToolRuntime

from deskpilot.graph.agent import build_agent_graph
from deskpilot.graph.prompts import STEP_BUDGET_MESSAGE, TOOL_FAILURE_MESSAGE
from deskpilot.graph.state import AgentState
from tests.support import ScriptedChatModel, ai_text, ai_with_tool_calls, scripted, tool_call


@dataclass(frozen=True)
class FakeContext:
    """Stands in for AgentContext: proves the context reaches tools untouched."""

    customer_email: str


@tool
async def echo_order(order_number: str) -> str:
    """Report an order's status. Says nothing about who is asking."""
    return f"{order_number}: shipped"


@tool
async def whoami(runtime: ToolRuntime[FakeContext]) -> str:
    """Report the identity the tool was given, to prove context injection works."""
    return runtime.context.customer_email


@tool
async def broken(order_number: str) -> str:
    """Always fails."""
    raise RuntimeError("connection to postgres at secret-host:5432 refused")


def start(question: str = "where is my order ORD-1042?") -> AgentState:
    return {
        "messages": [HumanMessage(question)],
        "steps": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "escalated": False,
    }


async def run(
    model: ScriptedChatModel,
    tools: list[BaseTool] | None = None,
    max_steps: int = 6,
    context: FakeContext | None = None,
) -> dict[str, Any]:
    graph = build_agent_graph(model, tools or [echo_order], max_steps=max_steps)
    return await graph.ainvoke(
        start(), context=context or FakeContext(customer_email="noah.kim@example.com")
    )


async def test_answers_directly_when_no_tool_is_needed() -> None:
    model = scripted([ai_text("Hello, how can I help?")])

    result = await run(model)

    assert result["steps"] == 1
    assert result["escalated"] is False
    assert isinstance(result["messages"][-1], AIMessage)
    assert result["messages"][-1].text == "Hello, how can I help?"


async def test_tool_call_result_comes_back_to_the_model() -> None:
    model = scripted(
        [
            ai_with_tool_calls(tool_call("echo_order", order_number="ORD-1042")),
            ai_text("Your order is on its way."),
        ]
    )

    result = await run(model)

    kinds = [type(message).__name__ for message in result["messages"]]
    assert kinds == ["HumanMessage", "AIMessage", "ToolMessage", "AIMessage"]
    assert result["steps"] == 2
    assert result["messages"][-1].text == "Your order is on its way."


async def test_context_is_injected_into_tools() -> None:
    model = scripted([ai_with_tool_calls(tool_call("whoami")), ai_text("Done.")])

    result = await run(
        model, tools=[whoami], context=FakeContext(customer_email="ana.garcia@example.com")
    )

    tool_message = next(m for m in result["messages"] if isinstance(m, ToolMessage))
    assert tool_message.content == "ana.garcia@example.com"


async def test_identity_is_never_shown_to_the_model() -> None:
    """The model can only learn the identity if a tool chooses to tell it."""
    model = scripted(
        [ai_with_tool_calls(tool_call("echo_order", order_number="ORD-1042")), ai_text("Done.")]
    )

    result = await run(model, context=FakeContext(customer_email="ana.garcia@example.com"))

    seen_by_model = [m for call in model.calls for m in call]
    assert not any("ana.garcia@example.com" in str(m.content) for m in seen_by_model)
    assert not any("ana.garcia@example.com" in str(m.content) for m in result["messages"])


async def test_system_prompt_is_sent_but_never_stored_in_state() -> None:
    model = scripted([ai_text("Hi.")])

    result = await run(model)

    assert isinstance(model.calls[0][0], SystemMessage)
    assert not any(isinstance(message, SystemMessage) for message in result["messages"])


async def test_tools_are_bound_to_the_model() -> None:
    model = scripted([ai_text("Hi.")])

    await run(model, tools=[echo_order, broken])

    assert model.bound_tools == ["echo_order", "broken"]


async def test_parallel_tool_calls_all_run() -> None:
    model = scripted(
        [
            ai_with_tool_calls(
                tool_call("echo_order", call_id="a", order_number="ORD-1042"),
                tool_call("echo_order", call_id="b", order_number="ORD-1031"),
            ),
            ai_text("Both are on their way."),
        ]
    )

    result = await run(model)

    results = [m.content for m in result["messages"] if isinstance(m, ToolMessage)]
    assert len(results) == 2
    assert any("ORD-1042" in str(content) for content in results)
    assert any("ORD-1031" in str(content) for content in results)


async def test_failing_tool_is_reported_to_the_model_without_internals() -> None:
    model = scripted(
        [
            ai_with_tool_calls(tool_call("broken", order_number="ORD-1042")),
            ai_text("Sorry, I could not look that up."),
        ]
    )

    result = await run(model, tools=[broken])

    tool_message = next(m for m in result["messages"] if isinstance(m, ToolMessage))
    assert tool_message.status == "error"
    assert tool_message.content == TOOL_FAILURE_MESSAGE
    assert "postgres" not in str(tool_message.content)
    assert "secret-host" not in str(tool_message.content)
    # The run continues: the model gets a chance to apologise.
    assert result["messages"][-1].text == "Sorry, I could not look that up."


async def test_unknown_tool_name_does_not_crash_the_run() -> None:
    model = scripted(
        [
            ai_with_tool_calls(tool_call("refund_everything", order_number="ORD-1042")),
            ai_text("I cannot do that."),
        ]
    )

    result = await run(model)

    tool_message = next(m for m in result["messages"] if isinstance(m, ToolMessage))
    assert tool_message.status == "error"
    assert result["messages"][-1].text == "I cannot do that."


async def test_step_budget_stops_a_looping_model() -> None:
    # This model only ever calls tools, so without a budget the loop never ends.
    model = scripted([ai_with_tool_calls(tool_call("echo_order", order_number="ORD-1042"))])

    result = await run(model, max_steps=3)

    assert result["steps"] == 3
    assert result["messages"][-1].text == STEP_BUDGET_MESSAGE
    assert result["escalated"] is True


@pytest.mark.parametrize("max_steps", [1, 2, 5])
async def test_step_budget_is_respected_exactly(max_steps: int) -> None:
    model = scripted([ai_with_tool_calls(tool_call("echo_order", order_number="ORD-1042"))])

    result = await run(model, max_steps=max_steps)

    assert result["steps"] == max_steps


async def test_token_usage_is_summed_across_model_calls() -> None:
    model = scripted(
        [
            ai_with_tool_calls(tool_call("echo_order", order_number="ORD-1042")),
            ai_text("On its way."),
        ]
    )

    result = await run(model)

    # Two model calls, each reporting 10 in and 5 out.
    assert result["input_tokens"] == 20
    assert result["output_tokens"] == 10
