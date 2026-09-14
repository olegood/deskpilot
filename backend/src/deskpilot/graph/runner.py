"""Running the agent graph and summarising the result.

`run_agent` takes a ready AgentContext and a thread id, so the caller owns both the
database engine and the checkpointer: the CLI opens them per command, and the web
API will keep them for the process's lifetime.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph

from deskpilot.config import ModelRole, Settings, get_settings
from deskpilot.db.models import TicketCategory
from deskpilot.graph.agent import build_agent_graph
from deskpilot.graph.classify import parse_category
from deskpilot.graph.context import AgentContext
from deskpilot.graph.state import AgentState
from deskpilot.llm import build_chat_model
from deskpilot.tools import ALL_TOOLS


@dataclass(frozen=True)
class AgentRun:
    """What one turn produced.

    Token counts cover the whole ticket, not just this turn, because that is what
    the checkpointed state accumulates and what a per-ticket budget will need.
    Everything else describes this turn only.
    """

    answer: str
    # This turn: the customer's message and everything that followed it.
    turn_messages: list[AnyMessage]
    # The whole conversation so far, oldest first.
    messages: list[AnyMessage] = field(default_factory=list)
    steps: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    escalated: bool = False
    # What the classifier made of the ticket. None if it has not run or failed.
    category: TicketCategory | None = None

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def tool_calls(self) -> list[str]:
        """Names of the tools called in this turn, in order."""
        return [
            call["name"]
            for message in self.turn_messages
            if isinstance(message, AIMessage)
            for call in message.tool_calls
        ]


def messages_since(messages: Sequence[AnyMessage], message_id: str) -> list[AnyMessage]:
    """The message with this id and everything after it."""
    for index, message in enumerate(messages):
        if message.id == message_id:
            return list(messages[index:])
    return list(messages)


def final_answer(messages: Iterable[AnyMessage]) -> str:
    """The last thing the agent said to the customer, ignoring tool-call turns."""
    for message in reversed(list(messages)):
        if isinstance(message, AIMessage) and not message.tool_calls:
            return message.text.strip()
    return ""


AgentGraph = CompiledStateGraph[AgentState, AgentContext, AgentState, AgentState]


async def run_turn(
    graph: AgentGraph,
    message: str,
    context: AgentContext,
    thread_id: str,
) -> AgentRun:
    """Add one customer message to a conversation and let the agent respond.

    Takes a compiled graph so a caller that handles many turns - the web API, the
    eval runner, a test - can build it once and reuse it.
    """
    # On a resumed thread these values merge into the saved state: messages are
    # appended, the token counters add zero, and steps is overwritten so this turn
    # gets a fresh budget.
    # An explicit id makes this turn's slice of the conversation findable afterwards.
    question = HumanMessage(message, id=f"turn-{uuid.uuid4()}")
    turn: AgentState = {
        "messages": [question],
        "steps": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "escalated": False,
        # category is deliberately absent: the channel has no reducer, so including
        # it would overwrite the category the classifier set on the first turn.
    }
    config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
    # The context is passed here, outside the state and outside the messages, so it
    # is never visible to the model and never written to a checkpoint.
    result = await graph.ainvoke(turn, config=config, context=context)

    messages = list(result["messages"])
    turn_messages = messages_since(messages, str(question.id))
    return AgentRun(
        answer=final_answer(turn_messages),
        turn_messages=turn_messages,
        messages=messages,
        steps=result["steps"],
        input_tokens=result["input_tokens"],
        output_tokens=result["output_tokens"],
        escalated=result["escalated"],
        category=parse_category(result.get("category")),
    )


async def run_agent(
    message: str,
    context: AgentContext,
    thread_id: str,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
    settings: Settings | None = None,
) -> AgentRun:
    """Build the configured agent and run one turn with it."""
    settings = settings or get_settings()
    graph = build_agent_graph(
        model=build_chat_model(ModelRole.AGENT, settings),
        tools=ALL_TOOLS,
        max_steps=settings.max_agent_steps,
        checkpointer=checkpointer,
        classifier=build_chat_model(ModelRole.CLASSIFIER, settings),
    )
    return await run_turn(graph, message, context, thread_id)
