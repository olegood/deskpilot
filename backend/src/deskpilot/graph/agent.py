"""The agent graph: a hand-built ReAct loop.

    START -> agent -> (tool calls? and budget left?) -> tools -> agent -> ...
                   -> (no tool calls) -> END
                   -> (budget spent)  -> over_budget -> END

The loop is bounded by a step budget so a model that keeps calling tools cannot run
forever. Full loop detection, per-call timeouts, and retries arrive with the
resilience milestone.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any, Final, Literal

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode

from deskpilot.graph.context import AgentContext
from deskpilot.graph.prompts import STEP_BUDGET_MESSAGE, SUPPORT_AGENT_PROMPT, TOOL_FAILURE_MESSAGE
from deskpilot.graph.state import AgentState

logger = logging.getLogger(__name__)

AGENT: Final = "agent"
TOOLS: Final = "tools"
OVER_BUDGET: Final = "over_budget"


def on_tool_error(exc: Exception) -> str:
    """What the model is told when a tool raises.

    The exception itself never reaches the model: it can contain connection strings,
    table names, or other internals.
    """
    logger.warning("tool call failed: %s: %s", type(exc).__name__, exc)
    return TOOL_FAILURE_MESSAGE


def build_agent_graph(
    model: BaseChatModel,
    tools: Sequence[BaseTool],
    max_steps: int,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
) -> CompiledStateGraph[AgentState, AgentContext, AgentState, AgentState]:
    """Build and compile the agent graph.

    With a checkpointer, state is persisted per thread and a ticket can be resumed
    across turns and across restarts. Without one, the run is in-memory only.
    """
    bound_model = model.bind_tools(list(tools))

    async def agent(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        """Call the model once, with the tools bound."""
        # The system prompt is prepended per call and never stored in state, so nothing
        # that appends to the conversation can push it out or edit it away.
        response = await bound_model.ainvoke(
            [SystemMessage(SUPPORT_AGENT_PROMPT), *state["messages"]], config
        )
        usage = getattr(response, "usage_metadata", None) or {}
        return {
            "messages": [response],
            # steps has no reducer, so this overwrites rather than adds.
            "steps": state["steps"] + 1,
            "input_tokens": usage.get("input_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0),
        }

    async def over_budget(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        """Stop a run that keeps calling tools, and tell the customer plainly."""
        logger.warning("step budget of %s exhausted; ending the run", max_steps)
        return {"messages": [AIMessage(STEP_BUDGET_MESSAGE)], "escalated": True}

    def route(state: AgentState) -> Literal["tools", "over_budget", "__end__"]:
        """Decide what happens after a model call."""
        last = state["messages"][-1]
        if not isinstance(last, AIMessage) or not last.tool_calls:
            return "__end__"
        if state["steps"] >= max_steps:
            return OVER_BUDGET
        return TOOLS

    builder = StateGraph(AgentState, context_schema=AgentContext)
    builder.add_node(AGENT, agent)
    # ToolNode injects AgentContext into tools as ToolRuntime, runs parallel tool
    # calls concurrently, and turns unknown tool names and bad arguments into error
    # ToolMessages the model can read and react to.
    builder.add_node(TOOLS, ToolNode(list(tools), handle_tool_errors=on_tool_error))
    builder.add_node(OVER_BUDGET, over_budget)

    builder.add_edge(START, AGENT)
    builder.add_conditional_edges(AGENT, route)
    builder.add_edge(TOOLS, AGENT)
    builder.add_edge(OVER_BUDGET, END)
    return builder.compile(checkpointer=checkpointer)
