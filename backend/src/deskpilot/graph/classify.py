"""Ticket classification.

A short, structured model call that runs once per ticket, before the agent loop.
The category is advisory: it is a hint in the prompt and a label for reporting, and
it never decides what the agent is allowed to do. See docs/decisions.md, D-042.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from deskpilot.db.models import TicketCategory
from deskpilot.graph.prompts import CLASSIFIER_PROMPT

logger = logging.getLogger(__name__)

# How much of the customer's message the classifier reads. A category never needs
# more than the opening, and a cap keeps one long message from costing a long call.
MAX_CLASSIFIED_CHARACTERS = 2000


class Classification(BaseModel):
    """The structured answer we ask the classifier for."""

    category: TicketCategory = Field(description="Which category best fits the message.")


@dataclass(frozen=True)
class ClassificationResult:
    """The category, plus what it cost. Classification is not free."""

    category: TicketCategory
    input_tokens: int = 0
    output_tokens: int = 0


def parse_category(value: object) -> TicketCategory | None:
    """Turn whatever came back from a checkpoint into a category, or None.

    State is serialized, so a category stored as a StrEnum is read back as a str.
    An unknown value means the enum changed since the checkpoint was written, which
    is a reason to treat the ticket as unclassified, not to fail.
    """
    if isinstance(value, TicketCategory):
        return value
    if isinstance(value, str):
        try:
            return TicketCategory(value)
        except ValueError:
            logger.warning("unknown stored category %r; treating as unclassified", value)
    return None


def first_customer_message(messages: list[AnyMessage]) -> str:
    """The text the ticket opened with, which is what defines its category."""
    for message in messages:
        if isinstance(message, HumanMessage):
            return message.text[:MAX_CLASSIFIED_CHARACTERS]
    return ""


async def classify(model: BaseChatModel, text: str) -> ClassificationResult:
    """Classify one message, falling back to OTHER rather than failing.

    A classification is a hint. Losing it costs a little answer quality; raising
    from here would cost the customer their reply, so every failure degrades to
    OTHER and is logged.
    """
    if not text.strip():
        return ClassificationResult(TicketCategory.OTHER)
    # include_raw keeps the underlying AIMessage. Without it the parsed object is all
    # that comes back and the call's token usage is silently lost, which would make
    # classification look free in the ticket's totals.
    structured = model.with_structured_output(Classification, include_raw=True)
    try:
        response = await structured.ainvoke([SystemMessage(CLASSIFIER_PROMPT), HumanMessage(text)])
    except Exception:
        logger.warning("classification failed; falling back to other", exc_info=True)
        return ClassificationResult(TicketCategory.OTHER)

    raw = response.get("raw") if isinstance(response, dict) else None
    usage = raw.usage_metadata if isinstance(raw, AIMessage) and raw.usage_metadata else None
    parsed = response.get("parsed") if isinstance(response, dict) else None
    if not isinstance(parsed, Classification):
        logger.warning("classifier returned no usable category; falling back to other")
    return ClassificationResult(
        category=parsed.category if isinstance(parsed, Classification) else TicketCategory.OTHER,
        input_tokens=usage["input_tokens"] if usage else 0,
        output_tokens=usage["output_tokens"] if usage else 0,
    )
