"""Unit tests for how policy passages are rendered for the model."""

from deskpilot.knowledge.search import PolicyPassage
from deskpilot.tools.policy import format_passages


def passage(heading: str, content: str, distance: float = 0.2) -> PolicyPassage:
    return PolicyPassage(
        document="refunds.md",
        heading=heading,
        content=content,
        distance=distance,
    )


def test_each_passage_names_its_source() -> None:
    """So an answer can be attributed, and a wrong answer traced back."""
    text = format_passages([passage("Return window", "Within 30 days.")])

    assert "refunds.md" in text
    assert "Return window" in text
    assert "Within 30 days." in text


def test_passages_are_separated_clearly() -> None:
    text = format_passages(
        [passage("Return window", "Within 30 days."), passage("Refunds", "To the original card.")]
    )

    assert "---" in text
    assert text.index("Within 30 days.") < text.index("To the original card.")
