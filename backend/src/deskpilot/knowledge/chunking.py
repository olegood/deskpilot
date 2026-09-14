"""Splitting policy markdown into passages worth embedding.

Two rules shape this:

- Split at headings, not at a fixed character count. A policy document is already
  organised into the questions people ask ("Return window", "Lost parcels"), so the
  author's structure is better than any window we could slide over the text.
- Every chunk repeats its heading path. Retrieved on its own, "within 30 days of
  delivery" is ambiguous; "Returns and refunds > Return window" makes it answerable.
  It also gives the embedding the topic words, which measurably helps matching.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

# A markdown ATX heading: one or more #, a space, then the text.
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*$")
# A blank line between paragraphs.
PARAGRAPH_BREAK = re.compile(r"\n\s*\n")


@dataclass(frozen=True)
class Chunk:
    """One passage, ready to embed."""

    document: str
    heading: str
    ordinal: int
    content: str


def sha256_of(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def split_sections(markdown: str) -> list[tuple[str, str]]:
    """Split into (heading path, body) pairs, one per heading.

    The heading path joins the enclosing headings, so a level-2 section under a
    level-1 title becomes "Title > Section". Text before the first heading is
    ignored: policy documents always start with one.
    """
    sections: list[tuple[str, str]] = []
    # Enclosing heading text by level, e.g. {1: "Returns and refunds"}.
    ancestors: dict[int, str] = {}
    heading_path: str | None = None
    body: list[str] = []

    def flush() -> None:
        if heading_path is not None and (text := "\n".join(body).strip()):
            sections.append((heading_path, text))

    for line in markdown.splitlines():
        match = HEADING.match(line)
        if match is None:
            body.append(line)
            continue
        flush()
        body = []
        level, title = len(match.group(1)), match.group(2).strip()
        # Drop any deeper headings we are no longer inside.
        ancestors = {lvl: text for lvl, text in ancestors.items() if lvl < level}
        ancestors[level] = title
        heading_path = " > ".join(ancestors[lvl] for lvl in sorted(ancestors))
    flush()
    return sections


def split_long_body(body: str, max_chars: int) -> list[str]:
    """Split an over-long section at paragraph boundaries.

    Paragraphs are never broken apart: a half sentence embeds badly and reads worse.
    A single paragraph longer than the limit is kept whole and left over-long.
    """
    paragraphs = [p.strip() for p in PARAGRAPH_BREAK.split(body) if p.strip()]
    parts: list[str] = []
    current: list[str] = []
    for paragraph in paragraphs:
        candidate = [*current, paragraph]
        if current and len("\n\n".join(candidate)) > max_chars:
            parts.append("\n\n".join(current))
            current = [paragraph]
        else:
            current = candidate
    if current:
        parts.append("\n\n".join(current))
    return parts


def chunk_document(document: str, markdown: str, max_chars: int) -> list[Chunk]:
    """Turn one markdown document into passages, in reading order."""
    chunks: list[Chunk] = []
    for heading, body in split_sections(markdown):
        for part in split_long_body(body, max_chars):
            chunks.append(
                Chunk(
                    document=document,
                    heading=heading,
                    ordinal=len(chunks),
                    content=f"{heading}\n\n{part}",
                )
            )
    return chunks


def read_documents(directory: Path) -> dict[str, str]:
    """Read every markdown file in a directory, keyed by file name."""
    if not directory.is_dir():
        raise FileNotFoundError(f"policy directory not found: {directory}")
    return {path.name: path.read_text(encoding="utf-8") for path in sorted(directory.glob("*.md"))}
