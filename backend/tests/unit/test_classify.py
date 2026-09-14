"""Unit tests for classification. No model and no database."""

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from deskpilot.db.models import TicketCategory
from deskpilot.graph.classify import (
    MAX_CLASSIFIED_CHARACTERS,
    first_customer_message,
    parse_category,
)


def test_parse_category_accepts_the_enum() -> None:
    assert parse_category(TicketCategory.WARRANTY) is TicketCategory.WARRANTY


def test_parse_category_accepts_the_string_a_checkpoint_returns() -> None:
    """State is serialized, so a StrEnum comes back as a plain str."""
    assert parse_category("warranty") is TicketCategory.WARRANTY


def test_parse_category_treats_an_unknown_value_as_unclassified() -> None:
    assert parse_category("cabbage") is None
    assert parse_category(None) is None
    assert parse_category(17) is None


def test_first_customer_message_ignores_everything_else() -> None:
    messages = [
        SystemMessage("system"),
        HumanMessage("My tent leaked."),
        AIMessage("Sorry to hear that."),
        HumanMessage("It is the second one."),
    ]

    assert first_customer_message(messages) == "My tent leaked."


def test_first_customer_message_is_capped() -> None:
    long_message = HumanMessage("x" * (MAX_CLASSIFIED_CHARACTERS * 2))

    assert len(first_customer_message([long_message])) == MAX_CLASSIFIED_CHARACTERS


def test_first_customer_message_handles_an_empty_conversation() -> None:
    assert first_customer_message([]) == ""
