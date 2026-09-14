"""Graph state: everything that is checkpointed and survives a restart."""

from __future__ import annotations

import operator
from typing import Annotated, NotRequired, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    """State of one ticket, checkpointed after every node.

    Reducers decide how a node's return value merges into the state:
    - messages are appended (and updated by id) by add_messages
    - token counters are summed, so they cover the whole ticket, not one turn
    - steps has no reducer, so it is overwritten; each turn starts it at 0 again,
      which gives every customer message its own fresh step budget
    """

    messages: Annotated[list[AnyMessage], add_messages]
    # Model calls in the current turn. Bounds the ReAct loop.
    steps: int
    # Token usage across the whole ticket. Full accounting arrives with the
    # observability milestone.
    input_tokens: Annotated[int, operator.add]
    output_tokens: Annotated[int, operator.add]
    # Set when the agent gave up and a human needs to take over.
    escalated: bool
    # What the ticket is about, decided once on the first turn and kept afterwards.
    #
    # Held as a plain string, not as TicketCategory. A checkpoint is serialized, and
    # a StrEnum comes back from it as a str, so typing this channel as the enum would
    # be a lie on every turn after the first. Conversion happens at the edges, in
    # parse_category.
    #
    # NotRequired on purpose: a turn's input omits the key entirely, because this
    # channel has no reducer and passing None would wipe the stored category.
    category: NotRequired[str | None]
