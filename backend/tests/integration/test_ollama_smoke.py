"""Smoke tests against the real Ollama server and the configured agent model.

They prove the model can do what the agent needs: emit a well-formed tool call,
use the tool result, and report token usage. Run with: uv run pytest -m integration
"""

import httpx
import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool
from pydantic import BaseModel

from deskpilot.config import ModelRole, Provider, Settings
from deskpilot.llm import build_chat_model

pytestmark = [pytest.mark.integration]

SYSTEM_PROMPT = (
    "You are a support assistant for the Acme Gear shop. "
    "use the available tools to look up facts. Never guess order details."
)


@tool
def get_order_status(order_id: str) -> str:
    """Look up the shipping status of an order by its ID, for example, ORD-1042."""
    return f"Order {order_id}: in transit, expected delivery 2026-09-15."


@pytest.fixture(scope="module")
def settings() -> Settings:
    """Read settings from backend/.env, after checking Ollama has the agent model."""
    settings = Settings()
    if settings.agent.provider is not Provider.OLLAMA:
        pytest.skip("the agent role is not configured for Ollama")
    base_url = str(settings.ollama_base_url).rstrip("/")
    try:
        response = httpx.get(f"{base_url}/api/tags", timeout=5.0)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        pytest.fail(f"Ollama is not reachable at {base_url}: {exc}. Is ollama-serve-sh running?")
    pulled = {m["name"] for m in response.json()["models"]}
    wanted = settings.agent.model
    if wanted not in pulled and f"{wanted}:latest" not in pulled:
        pytest.fail(f"model {wanted!r} is not pulled; run: ollama pull {wanted}")
    return settings


@pytest.fixture
def model(settings: Settings) -> BaseModel:
    return build_chat_model(ModelRole.AGENT, settings)


def conversation() -> list[BaseMessage]:
    return [SystemMessage(SYSTEM_PROMPT), HumanMessage("Hi, where is my order ORD-1042?")]


async def test_model_emits_a_well_formed_tool_call(model: BaseChatModel):
    response = await model.bind_tools([get_order_status]).ainvoke(conversation())

    assert isinstance(response, AIMessage)
    assert len(response.tool_calls) == 1, f"expected one tool call, got {response!r}"
    call = response.tool_calls[0]
    assert call["name"] == "get_order_status"
    assert call["args"] == {"order_id": "ORD-1042"}


async def test_model_uses_the_tool_result(model: BaseChatModel):
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


async def test_model_reports_token_usage(model: BaseChatModel):
    response = await model.ainvoke(conversation())

    assert isinstance(response, AIMessage)
    assert response.usage_metadata is not None
    assert response.usage_metadata["input_tokens"] > 0
    assert response.usage_metadata["output_tokens"] > 0
