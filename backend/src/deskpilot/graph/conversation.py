"""Reading a ticket's conversation back out of its checkpoint.

Shared by the CLI and the API, so both render the same thing. No model and no
graph: this only reads state, so it costs nothing and works when Ollama is down.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.base import BaseCheckpointSaver


@dataclass(frozen=True)
class Turn:
    """One line of a conversation, as somebody reading it would see it."""

    # "customer", "agent", "tool_call", or "tool_result".
    speaker: str
    text: str


async def load_messages(checkpointer: BaseCheckpointSaver[Any], thread_id: str) -> list[AnyMessage]:
    saved = await checkpointer.aget_tuple({"configurable": {"thread_id": thread_id}})
    if saved is None:
        return []
    messages: list[AnyMessage] = list(saved.checkpoint["channel_values"].get("messages", []))
    return messages


def to_turns(messages: list[AnyMessage], include_tools: bool = False) -> list[Turn]:
    """Render messages for a person.

    Tool calls and their results are left out by default. They are the agent's
    working, not the conversation, and a customer has no use for them.
    """
    turns: list[Turn] = []
    for message in messages:
        if isinstance(message, HumanMessage):
            turns.append(Turn("customer", message.text))
        elif isinstance(message, AIMessage) and message.text.strip():
            turns.append(Turn("agent", message.text.strip()))
        elif include_tools and isinstance(message, AIMessage) and message.tool_calls:
            turns.extend(
                Turn("tool_call", f"{call['name']}({call['args']})") for call in message.tool_calls
            )
        elif include_tools and isinstance(message, ToolMessage):
            turns.append(Turn("tool_result", str(message.content)))
    return turns
