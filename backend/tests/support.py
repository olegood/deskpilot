"""Test doubles and helpers shared by the test suites.

ScriptedChatModel lets graph logic be tested without an LLM: it returns prepared
responses in order and records what it was asked, so the tests are fast and fully
deterministic. Real-model behaviour is covered by the integration tests instead.

invoke_tool runs a single tool the way the graph does, so tools can be tested with
their injected context.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from typing import Annotated, Any, TypedDict

from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_core.messages import AIMessage, AnyMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable, RunnableLambda
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deskpilot.authz.principal import Principal
from deskpilot.db.models import Customer, Region, UserRole


def tool_call(name: str, call_id: str = "call-1", **args: Any) -> dict[str, Any]:
    """Build one tool call for a scripted AIMessage."""
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


def ai_with_tool_calls(*calls: dict[str, Any]) -> AIMessage:
    return AIMessage("", tool_calls=list(calls), usage_metadata=usage())


def ai_text(text: str) -> AIMessage:
    return AIMessage(text, usage_metadata=usage())


def usage(input_tokens: int = 10, output_tokens: int = 5) -> dict[str, int]:
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
    }


class ScriptedChatModel(BaseChatModel):
    """Returns the scripted responses in order, repeating the last one if it runs out."""

    responses: list[AIMessage]
    # Every list of messages the model was asked to answer, for assertions.
    calls: list[list[BaseMessage]] = Field(default_factory=list)
    # Names of the tools the graph bound to the model, for assertions.
    bound_tools: list[str] = Field(default_factory=list)
    # Unique per instance, so two models in one conversation never emit the same
    # message id. Colliding ids make add_messages overwrite instead of append.
    run_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool],
        **kwargs: Any,
    ) -> Runnable[LanguageModelInput, BaseMessage]:
        self.bound_tools = [getattr(tool, "name", str(tool)) for tool in tools]
        return self

    def _next(self, messages: list[BaseMessage]) -> ChatResult:
        self.calls.append(list(messages))
        turn = len(self.calls)
        index = min(turn - 1, len(self.responses) - 1)
        # A fresh copy with unique ids every turn. Reusing one message object would
        # make add_messages treat the second turn as an edit of the first, and would
        # reuse tool_call ids across turns.
        template = self.responses[index]
        response = template.model_copy(
            update={
                "id": f"scripted-{self.run_id}-{turn}",
                "tool_calls": [
                    {**call, "id": f"{call['id']}-{self.run_id}-{turn}"}
                    for call in template.tool_calls
                ],
            }
        )
        return ChatResult(generations=[ChatGeneration(message=response)])

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        return self._next(messages)

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        return self._next(messages)


class ScriptedClassifier(BaseChatModel):
    """Stands in for the classifier model.

    with_structured_output is overridden rather than relying on the base
    implementation, which would need real tool calling. It returns the same shape
    the real one does with include_raw=True, so the token accounting in classify()
    is exercised too.
    """

    category: str = "other"
    # Raise instead of answering, to exercise the fallback path.
    fails: bool = False
    calls: list[list[BaseMessage]] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "scripted-classifier"

    def with_structured_output(
        self, schema: Any, *, include_raw: bool = False, **kwargs: Any
    ) -> Runnable[LanguageModelInput, Any]:
        def answer(messages: LanguageModelInput) -> Any:
            if isinstance(messages, list):
                self.calls.append([m for m in messages if isinstance(m, BaseMessage)])
            if self.fails:
                raise RuntimeError("classifier is unavailable")
            parsed = schema(category=self.category)
            raw = AIMessage(f'{{"category": "{self.category}"}}', usage_metadata=usage(7, 3))
            return {"raw": raw, "parsed": parsed, "parsing_error": None} if include_raw else parsed

        return RunnableLambda(answer)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        raise NotImplementedError("the classifier is only used through structured output")


def scripted(responses: Sequence[AIMessage]) -> ScriptedChatModel:
    return ScriptedChatModel(responses=list(responses))


async def invoke_tool(tool: BaseTool, context: Any, /, **args: Any) -> ToolMessage:
    """Run one tool exactly as the graph would, with its context injected.

    Tools that declare a ToolRuntime parameter cannot simply be awaited: the runtime
    is supplied by ToolNode during graph execution. This wraps a one-node graph
    around the call so tests exercise the real path.
    """

    class State(TypedDict):
        messages: Annotated[list[AnyMessage], add_messages]

    builder: StateGraph[State, Any, State, State] = StateGraph(State, context_schema=type(context))
    builder.add_node("tools", ToolNode([tool]))
    builder.add_edge(START, "tools")
    builder.add_edge("tools", END)

    request = AIMessage("", tool_calls=[tool_call(tool.name, **args)])
    result = await builder.compile().ainvoke({"messages": [request]}, context=context)
    return next(m for m in result["messages"] if isinstance(m, ToolMessage))


def fake_principal(
    email: str = "noah.kim@example.com",
    customer_id: int = 4,
    region: Region | None = Region.NA,
) -> Principal:
    """A customer principal built without a database, for graph tests."""
    return Principal(
        user_id=1,
        email=email,
        role=UserRole.CUSTOMER,
        is_active=True,
        customer_id=customer_id,
        home_region=region,
    )


async def principal_for(sessions: async_sessionmaker[AsyncSession], email: str) -> Principal:
    """A customer principal read from the seeded database, for integration tests."""
    async with sessions() as session:
        customer = await session.scalar(select(Customer).where(Customer.email == email))
    assert customer is not None, f"no seeded customer {email}"
    return Principal.for_customer(customer)
