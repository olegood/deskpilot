"""Unit tests for eval scoring. No model, no database."""

from deskpilot.db.models import TicketCategory
from deskpilot.evals.dataset import EvalCase
from deskpilot.evals.scoring import crashed, score, summarise
from deskpilot.graph.runner import AgentRun
from tests.support import ai_text, ai_with_tool_calls, tool_call


def case(**overrides: object) -> EvalCase:
    defaults: dict[str, object] = {
        "id": "example",
        "customer": "noah.kim@example.com",
        "message": "where is ORD-1042?",
        "category": TicketCategory.SHIPPING,
    }
    return EvalCase(**{**defaults, **overrides})  # type: ignore[arg-type]


def run(
    answer: str = "It has shipped.",
    tools: list[str] | None = None,
    category: TicketCategory | None = TicketCategory.SHIPPING,
    escalated: bool = False,
) -> AgentRun:
    messages = [ai_with_tool_calls(*(tool_call(name) for name in tools))] if tools else []
    return AgentRun(
        answer=answer,
        turn_messages=[*messages, ai_text(answer)],
        category=category,
        escalated=escalated,
    )


def test_a_matching_run_passes() -> None:
    result = score(case(requires=["get_order"]), run(tools=["get_order"]), 1.0)

    assert result.passed
    assert result.failures == []


def test_a_missing_tool_fails() -> None:
    result = score(case(requires=["get_order"]), run(tools=[]), 1.0)

    assert not result.passed
    assert "did not call get_order" in result.failures


def test_a_forbidden_tool_is_a_critical_failure() -> None:
    result = score(case(forbids=["get_order"]), run(tools=["get_order"]), 1.0)

    assert not result.passed
    assert result.critical_failures


def test_an_extra_tool_is_reported_but_not_failed() -> None:
    result = score(case(requires=["get_order"]), run(tools=["get_order", "get_customer"]), 1.0)

    assert result.passed
    assert result.extra_tools == ["get_customer"]


def test_a_leaked_string_is_a_critical_failure() -> None:
    result = score(case(answer_excludes=["ST-100001"]), run(answer="Tracking ST-100001."), 1.0)

    assert not result.passed
    assert result.critical_failures


def test_a_missing_string_fails_but_is_not_critical() -> None:
    result = score(case(answer_contains=["30 days"]), run(answer="Soon."), 1.0)

    assert not result.passed
    assert result.critical_failures == []


def test_substring_checks_ignore_case() -> None:
    result = score(case(answer_contains=["SHIPPED"]), run(answer="It has shipped."), 1.0)

    assert result.passed


def test_a_wrong_category_fails() -> None:
    result = score(case(), run(category=TicketCategory.WARRANTY), 1.0)

    assert "category: expected shipping, got warranty" in result.failures


def test_a_case_without_a_category_expectation_ignores_it() -> None:
    result = score(case(category=None), run(category=TicketCategory.WARRANTY), 1.0)

    assert result.passed


def test_escalation_fails_the_case() -> None:
    result = score(case(), run(escalated=True), 1.0)

    assert "the agent escalated instead of answering" in result.failures


def test_a_crashed_case_is_a_failure_not_an_exception() -> None:
    result = crashed(case(), RuntimeError("ollama went away"), 2.0)

    assert not result.passed
    assert result.error == "ollama went away"
    assert result.seconds == 2.0


def test_summary_counts_what_matters() -> None:
    results = [
        score(case(requires=["get_order"]), run(tools=["get_order"]), 1.0),
        score(case(forbids=["get_order"]), run(tools=["get_order"]), 2.0),
        score(case(), run(category=TicketCategory.WARRANTY), 3.0),
    ]

    summary = summarise(results)

    assert summary.total == 3
    assert summary.passed == 1
    assert summary.critical == 1
    assert summary.category_correct == 2
    assert summary.seconds == 6.0
