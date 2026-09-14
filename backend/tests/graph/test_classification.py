"""Classification inside the graph: when it runs, what it costs, how it fails."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver

from deskpilot.db.models import TicketCategory
from deskpilot.graph.agent import build_agent_graph
from deskpilot.graph.state import AgentState
from tests.support import ScriptedChatModel, ScriptedClassifier, ai_text, scripted

THREAD = "ticket-thread-1"


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


def build(model: ScriptedChatModel, classifier: ScriptedClassifier | None) -> Any:
    return build_agent_graph(
        model, [echo_order], max_steps=6, checkpointer=InMemorySaver(), classifier=classifier
    )


async def run(graph: Any, message: str) -> dict[str, Any]:
    return await graph.ainvoke(
        turn(message),
        config={"configurable": {"thread_id": THREAD}},
        context=FakeContext(customer_email="noah.kim@example.com"),
    )


async def test_the_first_turn_is_classified() -> None:
    graph = build(
        scripted([ai_text("Sorry to hear that.")]), ScriptedClassifier(category="warranty")
    )

    result = await run(graph, "The zip on my tent broke.")

    assert result["category"] == TicketCategory.WARRANTY.value


async def test_the_category_reaches_the_model_as_a_hint() -> None:
    model = scripted([ai_text("Sorry to hear that.")])
    graph = build(model, ScriptedClassifier(category="warranty"))

    await run(graph, "The zip on my tent broke.")

    system = model.calls[0][0]
    assert isinstance(system, SystemMessage)
    assert "warranty" in system.text
    # Hedged deliberately: the label comes from a model reading untrusted text.
    assert "hint" in system.text


async def test_later_turns_are_not_reclassified() -> None:
    classifier = ScriptedClassifier(category="warranty")
    graph = build(scripted([ai_text("One."), ai_text("Two.")]), classifier)

    await run(graph, "The zip on my tent broke.")
    result = await run(graph, "So what happens now?")

    assert len(classifier.calls) == 1
    assert result["category"] == TicketCategory.WARRANTY.value


async def test_classification_tokens_count_towards_the_ticket() -> None:
    graph = build(
        scripted([ai_text("Sorry to hear that.")]), ScriptedClassifier(category="warranty")
    )

    result = await run(graph, "The zip on my tent broke.")

    # 7 in and 3 out from the classifier, 10 and 5 from the agent.
    assert result["input_tokens"] == 17
    assert result["output_tokens"] == 8


async def test_a_failing_classifier_does_not_cost_the_customer_their_reply() -> None:
    graph = build(scripted([ai_text("How can I help?")]), ScriptedClassifier(fails=True))

    result = await run(graph, "The zip on my tent broke.")

    assert result["category"] == TicketCategory.OTHER.value
    assert result["messages"][-1].text == "How can I help?"


async def test_without_a_classifier_the_graph_still_runs() -> None:
    model = scripted([ai_text("How can I help?")])
    graph = build(model, None)

    result = await run(graph, "Hello.")

    assert result.get("category") is None
    system = model.calls[0][0]
    assert isinstance(system, SystemMessage)
    assert "classifier labelled" not in system.text
