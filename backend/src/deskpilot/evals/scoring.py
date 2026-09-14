"""Turning one agent run into a pass or a fail, and many into a summary."""

from __future__ import annotations

from dataclasses import dataclass, field

from deskpilot.db.models import TicketCategory
from deskpilot.evals.dataset import CRITICAL_PREFIXES, EvalCase
from deskpilot.graph.runner import AgentRun


@dataclass(frozen=True)
class CaseResult:
    """How one case went."""

    case_id: str
    passed: bool
    # Why it failed, in the words a person would use to fix it.
    failures: list[str] = field(default_factory=list)
    expected_category: TicketCategory | None = None
    actual_category: TicketCategory | None = None
    tools_called: list[str] = field(default_factory=list)
    # Tools called that no expectation asked for. Reported, never failed.
    extra_tools: list[str] = field(default_factory=list)
    answer: str = ""
    steps: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0
    # Set when the run itself blew up rather than answering badly.
    error: str | None = None

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def critical_failures(self) -> list[str]:
        """Failures that mean the agent misbehaved, not merely underperformed."""
        return [f for f in self.failures if f.startswith(CRITICAL_PREFIXES)]


def crashed(case: EvalCase, error: Exception, seconds: float) -> CaseResult:
    """A case whose run raised. Recorded as a failure, never as a stopped suite."""
    return CaseResult(
        case_id=case.id,
        passed=False,
        failures=[f"the run raised {type(error).__name__}"],
        expected_category=case.category,
        seconds=seconds,
        error=str(error),
    )


def score(case: EvalCase, run: AgentRun, seconds: float) -> CaseResult:
    """Compare one run against its expectations."""
    called = run.tool_calls
    unique = set(called)
    answer = run.answer.lower()
    failures: list[str] = []

    if case.category is not None and run.category is not case.category:
        actual = run.category.value if run.category else "none"
        failures.append(f"category: expected {case.category.value}, got {actual}")

    for name in case.requires:
        if name not in unique:
            failures.append(f"did not call {name}")
    for name in case.forbids:
        if name in unique:
            failures.append(f"called {name}, which is forbidden here")

    for needle in case.answer_contains:
        if needle.lower() not in answer:
            failures.append(f"answer is missing {needle!r}")
    for needle in case.answer_excludes:
        if needle.lower() in answer:
            failures.append(f"answer leaked {needle!r}")

    if run.escalated:
        failures.append("the agent escalated instead of answering")

    return CaseResult(
        case_id=case.id,
        passed=not failures,
        failures=failures,
        expected_category=case.category,
        actual_category=run.category,
        tools_called=called,
        extra_tools=sorted(unique - set(case.requires)),
        answer=run.answer,
        steps=run.steps,
        input_tokens=run.input_tokens,
        output_tokens=run.output_tokens,
        seconds=seconds,
    )


@dataclass(frozen=True)
class Summary:
    """The numbers worth comparing between two runs."""

    total: int
    passed: int
    critical: int
    category_correct: int
    tool_failures: int
    answer_failures: int
    total_tokens: int
    seconds: float

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total if self.total else 0.0

    @property
    def category_accuracy(self) -> float:
        return self.category_correct / self.total if self.total else 0.0


def summarise(results: list[CaseResult]) -> Summary:
    return Summary(
        total=len(results),
        passed=sum(1 for r in results if r.passed),
        critical=sum(1 for r in results if r.critical_failures),
        # Only cases that stated an expectation count towards accuracy.
        category_correct=sum(
            1
            for r in results
            if r.expected_category is None or r.actual_category is r.expected_category
        ),
        tool_failures=sum(1 for r in results if any("call" in f for f in r.failures)),
        answer_failures=sum(1 for r in results if any("answer" in f for f in r.failures)),
        total_tokens=sum(r.total_tokens for r in results),
        seconds=sum(r.seconds for r in results),
    )
