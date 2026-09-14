"""Eval cases, and checking that they are internally consistent.

A case is one customer message with expectations about how the agent should handle
it. Cases live in JSONL so they can be appended to, reviewed in a diff, and kept
under version control next to the code they test.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from deskpilot.config import BACKEND_DIR
from deskpilot.db.models import TicketCategory

DATASETS_DIR = BACKEND_DIR / "evals"
TOOL_SELECTION = DATASETS_DIR / "tool_selection.jsonl"


class DatasetError(Exception):
    """Raised when a dataset cannot be loaded or does not make sense."""


@dataclass(frozen=True)
class EvalCase:
    """One message, and what the agent is expected to do with it."""

    id: str
    customer: str
    message: str
    # What the classifier should call this ticket. None means the case does not
    # care: some cases exist only to check that something never happens.
    category: TicketCategory | None = None
    # Tools that must be called. Extra calls are reported but not failed: there is
    # usually more than one reasonable path to the same answer.
    requires: list[str] = field(default_factory=list)
    # Tools that must not be called. This is where the hard expectations live.
    forbids: list[str] = field(default_factory=list)
    # Case-insensitive substrings the answer must contain.
    answer_contains: list[str] = field(default_factory=list)
    # Case-insensitive substrings the answer must not contain. Used for the cases
    # where a wrong answer is worse than no answer, such as leaking another
    # customer's data.
    answer_excludes: list[str] = field(default_factory=list)
    # Free-form labels for selecting subsets, e.g. "security" or "policy". Running
    # the whole suite against a local model takes minutes, so filtering matters.
    tags: list[str] = field(default_factory=list)


# Failures in these areas are critical: the agent did something it must never do,
# rather than something it could have done better.
CRITICAL_PREFIXES = ("called", "answer leaked")


def load_cases(path: Path = TOOL_SELECTION) -> list[EvalCase]:
    """Read a JSONL dataset. Raises DatasetError with the line number on bad input."""
    if not path.exists():
        raise DatasetError(f"no dataset at {path}")
    cases = list(_read(path))
    if not cases:
        raise DatasetError(f"{path.name} has no cases")
    duplicates = {case.id for case in cases if [c.id for c in cases].count(case.id) > 1}
    if duplicates:
        raise DatasetError(f"duplicate case ids in {path.name}: {sorted(duplicates)}")
    return cases


def _read(path: Path) -> Iterator[EvalCase]:
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
            category = raw.get("category")
            yield EvalCase(**{**raw, "category": TicketCategory(category) if category else None})
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise DatasetError(f"{path.name} line {number}: {exc}") from exc


def validate_against(cases: list[EvalCase], customers: set[str], tools: set[str]) -> None:
    """Check every case refers to a customer and tools that actually exist.

    Catches the common ways a dataset rots: a renamed tool, a seed customer that
    was removed, a typo in an expectation that would otherwise fail silently and be
    written off as a model problem.
    """
    problems: list[str] = []
    for case in cases:
        if case.customer not in customers:
            problems.append(f"{case.id}: unknown customer {case.customer!r}")
        for name in [*case.requires, *case.forbids]:
            if name not in tools:
                problems.append(f"{case.id}: unknown tool {name!r}")
        if set(case.requires) & set(case.forbids):
            problems.append(f"{case.id}: a tool is both required and forbidden")
        if not case.message.strip():
            problems.append(f"{case.id}: empty message")
    if problems:
        raise DatasetError("; ".join(problems))
