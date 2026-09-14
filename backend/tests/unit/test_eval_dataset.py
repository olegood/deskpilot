"""The eval dataset checks itself. Fast, and runs with the normal test suite.

A rotten dataset is worse than no dataset: a case that names a renamed tool fails
for ever and gets written off as a model problem. These tests catch that the moment
it happens, rather than the next time somebody runs the suite.
"""

import pytest

from deskpilot.db.seed import CUSTOMERS
from deskpilot.evals.dataset import DatasetError, EvalCase, load_cases, validate_against
from deskpilot.evals.runner import select
from deskpilot.tools import ALL_TOOLS

CUSTOMER_EMAILS = {customer.email for customer in CUSTOMERS}
TOOL_NAMES = {tool.name for tool in ALL_TOOLS}


def test_the_dataset_loads() -> None:
    assert len(load_cases()) >= 10


def test_every_case_names_a_real_customer_and_real_tools() -> None:
    validate_against(load_cases(), CUSTOMER_EMAILS, TOOL_NAMES)


def test_case_ids_are_unique() -> None:
    cases = load_cases()
    assert len({case.id for case in cases}) == len(cases)


def test_the_suite_covers_the_tools_and_a_security_case() -> None:
    cases = load_cases()
    exercised = {name for case in cases for name in case.requires}
    assert exercised >= TOOL_NAMES, f"no case requires {sorted(TOOL_NAMES - exercised)}"
    assert any("security" in case.tags for case in cases)


def test_validation_rejects_an_unknown_tool() -> None:
    case = EvalCase(id="x", customer="noah.kim@example.com", message="hi", requires=["nope"])
    with pytest.raises(DatasetError, match="unknown tool"):
        validate_against([case], CUSTOMER_EMAILS, TOOL_NAMES)


def test_validation_rejects_a_contradictory_case() -> None:
    case = EvalCase(
        id="x",
        customer="noah.kim@example.com",
        message="hi",
        requires=["get_order"],
        forbids=["get_order"],
    )
    with pytest.raises(DatasetError, match="both required and forbidden"):
        validate_against([case], CUSTOMER_EMAILS, TOOL_NAMES)


def test_select_filters_by_id_then_tag() -> None:
    cases = load_cases()
    by_id = select(cases, ["greeting"], [])
    by_tag = select(cases, [], ["security"])

    assert [case.id for case in by_id] == ["greeting"]
    assert by_tag
    assert all("security" in case.tags for case in by_tag)
    assert select(cases, [], []) == cases
