"""Unit tests for splitting policy markdown into passages. No database, no model."""

from deskpilot.knowledge.chunking import (
    chunk_document,
    sha256_of,
    split_long_body,
    split_sections,
)

DOCUMENT = """# Returns and refunds

## Return window

Customers may return most items within 30 days.

Gold tier customers have 60 days.

## How refunds are issued

Refunds go to the original payment method.

### Expired cards

Contact your bank.

## Condition

Items must be unused.
"""


def test_sections_carry_their_heading_path() -> None:
    headings = [heading for heading, _ in split_sections(DOCUMENT)]

    assert headings == [
        "Returns and refunds > Return window",
        "Returns and refunds > How refunds are issued",
        "Returns and refunds > How refunds are issued > Expired cards",
        "Returns and refunds > Condition",
    ]


def test_a_deeper_heading_does_not_leak_into_the_next_sibling() -> None:
    """After a level-3 section, the next level-2 must not still carry it."""
    sections = dict(split_sections(DOCUMENT))

    assert "Returns and refunds > Condition" in sections
    assert "Expired cards" not in "Returns and refunds > Condition"


def test_a_title_with_no_body_produces_no_section() -> None:
    """The level-1 title has no text of its own, only subsections."""
    headings = [heading for heading, _ in split_sections(DOCUMENT)]

    assert "Returns and refunds" not in headings


def test_every_chunk_repeats_its_heading() -> None:
    """Retrieved alone, a passage has to say what it is about."""
    chunks = chunk_document("returns.md", DOCUMENT, max_chars=1200)

    for chunk in chunks:
        assert chunk.content.startswith(chunk.heading)


def test_chunks_are_numbered_in_reading_order() -> None:
    chunks = chunk_document("returns.md", DOCUMENT, max_chars=1200)

    assert [chunk.ordinal for chunk in chunks] == list(range(len(chunks)))
    assert all(chunk.document == "returns.md" for chunk in chunks)


def test_a_long_section_is_split_at_paragraph_boundaries() -> None:
    body = "\n\n".join(f"Paragraph number {index} with some words in it." for index in range(10))

    parts = split_long_body(body, max_chars=120)

    assert len(parts) > 1
    # No paragraph was cut in half.
    assert sum(part.count("Paragraph number") for part in parts) == 10
    for part in parts:
        assert not part.startswith("with some words")


def test_one_huge_paragraph_is_kept_whole() -> None:
    """Better an over-long passage than half a sentence."""
    body = "word " * 500

    parts = split_long_body(body, max_chars=100)

    assert len(parts) == 1


def test_a_short_section_is_not_split() -> None:
    assert split_long_body("One line.", max_chars=1200) == ["One line."]


def test_digest_changes_with_content() -> None:
    assert sha256_of("a") == sha256_of("a")
    assert sha256_of("a") != sha256_of("b")
