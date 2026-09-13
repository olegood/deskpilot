"""Smoke tests against the real Ollama server and the configured agent model.

They prove the model can do what the agent needs: emit a well-formed tool call,
use the tool result, and report token usage. Run with: uv run pytest -m integration
"""

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool

from deskpilot.config import ModelRole, Settings
from deskpilot.llm import build_chat_model

pytestmark = pytest.mark.integration

SYSTEM_PROMPT = (
    "You are a support assistant for the Acme Gear shop. "
    "Use the available tools to look up facts. Never guess order details."
)


@tool
def get_order_status(order_id: str) -> str:
    """Look up the shipping status of an order by its ID, for example ORD-1042."""
    return f"Order {order_id}: in transit, expected delivery 2026-09-15."


@pytest.fixture
def model(agent_settings: Settings) -> BaseChatModel:
    return build_chat_model(ModelRole.AGENT, agent_settings)


def conversation() -> list[BaseMessage]:
    return [SystemMessage(SYSTEM_PROMPT), HumanMessage("Hi, where is my order ORD-1042?")]


async def test_model_emits_a_well_formed_tool_call(model: BaseChatModel) -> None:
    response = await model.bind_tools([get_order_status]).ainvoke(conversation())

    assert isinstance(response, AIMessage)
    assert len(response.tool_calls) == 1, f"expected one tool call, got: {response!r}"
    call = response.tool_calls[0]
    assert call["name"] == "get_order_status"
    assert call["args"] == {"order_id": "ORD-1042"}


async def test_model_uses_the_tool_result(model: BaseChatModel) -> None:
    bound = model.bind_tools([get_order_status])
    messages = conversation()

    first = await bound.ainvoke(messages)
    assert isinstance(first, AIMessage)
    assert first.tool_calls, f"expected a tool call, got: {first!r}"
    # Invoking a tool with a ToolCall returns a ToolMessage linked by tool_call_id.
    tool_message = await get_order_status.ainvoke(first.tool_calls[0])

    final = await bound.ainvoke([*messages, first, tool_message])

    assert isinstance(final, AIMessage)
    assert not final.tool_calls
    assert "transit" in final.text.lower()
    assert "<think>" not in final.text


async def test_model_reports_token_usage(model: BaseChatModel) -> None:
    response = await model.ainvoke(conversation())

    assert isinstance(response, AIMessage)
    assert response.usage_metadata is not None
    assert response.usage_metadata["input_tokens"] > 0
    assert response.usage_metadata["output_tokens"] > 0
