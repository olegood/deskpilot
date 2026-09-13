"""Graph state: everything that is checkpointed and survives a restart."""

from __future__ import annotations

import operator
from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    """State of one agent run.

    Reducers decide how a node's return value merges into the state:
    - messages are appended (and updated by id) by add_message
    - counters are summed, so a node returns the delta, not the new total
    """

    messages: Annotated[list[AnyMessage], add_messages]
    # Number of model calls so far. Bounds the ReAct loop.
    steps: Annotated[int, operator.add]
    # Token usage for this run. Full accounting arrives with the observability milestone.
    input_tokens: Annotated[int, operator.add]
    output_tokens: Annotated[int, operator.add]
