"""Running the agent graph and summarising the result.

`answer_question` takes a ready AgentContext, so the caller owns the database
engine's lifecycle: the CLI opens one per command, and the web API will reuse a
shared one.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage

from deskpilot.config import ModelRole, Settings, get_settings
from deskpilot.graph.agent import build_agent_graph
from deskpilot.graph.context import AgentContext
from deskpilot.graph.state import AgentState
from deskpilot.llm import build_chat_model
from deskpilot.tools import ALL_TOOLS


@dataclass(frozen=True)
class AgentRun:
    """What one run produced: enough for the CLI now and for evals later."""

    answer: str
    messages: list[AnyMessage]
    steps: int
    input_tokens: int
    output_tokens: int

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def tool_calls(self) -> list[str]:
        """Names of the tools the agent called, in order."""
        return [
            call["name"]
            for message in self.messages
            if isinstance(message, AIMessage)
            for call in message.tool_calls
        ]


def final_answer(messages: Iterable[AnyMessage]) -> str:
    """The last thing the agent said to the customer, ignoring tool-call turns."""
    for message in reversed(list(messages)):
        if isinstance(message, AIMessage) and not message.tool_calls:
            return message.text.strip()
    return ""


async def answer_question(
    question: str,
    context: AgentContext,
    settings: Settings | None = None,
) -> AgentRun:
    """Answer one customer question with the agent."""
    settings = settings or get_settings()
    graph = build_agent_graph(
        model=build_chat_model(ModelRole.AGENT, settings),
        tools=ALL_TOOLS,
        max_steps=settings.max_agent_steps,
    )
    initial: AgentState = {
        "messages": [HumanMessage(question)],
        "steps": 0,
        "input_tokens": 0,
        "output_tokens": 0,
    }
    # The context is passed here, outside the state and outside the messages, so it
    # is never visible to the model and never written to a checkpoint.
    result = await graph.ainvoke(initial, context=context)

    messages = list(result["messages"])
    return AgentRun(
        answer=final_answer(messages),
        messages=messages,
        steps=result["steps"],
        input_tokens=result["input_tokens"],
        output_tokens=result["output_tokens"],
    )
