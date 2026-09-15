"""Unit tests for what gets recorded and how it is described. No database."""

import pytest

from deskpilot.authz import resources
from deskpilot.authz.actions import ALWAYS_AUDITED, Action
from deskpilot.authz.audit import MAX_RESOURCE, describe, should_record
from deskpilot.authz.engine import Decision
from deskpilot.db.models import Region

ALLOWED = Decision(allowed=True, reason="fine", policy="a_rule")
DENIED = Decision(allowed=False, reason="no", policy="a_rule")


@pytest.mark.parametrize("action", list(Action))
def test_every_denial_is_recorded(action: Action) -> None:
    """No exceptions. A refusal is the thing somebody will come looking for."""
    assert should_record(action, DENIED)


def test_a_routine_allow_is_not_recorded() -> None:
    """A customer reading their own order happens on every turn of every ticket."""
    assert not should_record(Action.ORDER_VIEW, ALLOWED)
    assert not should_record(Action.POLICY_SEARCH, ALLOWED)
    assert not should_record(Action.TICKET_REPLY, ALLOWED)


@pytest.mark.parametrize("action", sorted(ALWAYS_AUDITED))
def test_a_consequential_allow_is_recorded(action: Action) -> None:
    assert should_record(action, ALLOWED)


def test_money_and_administration_are_always_audited() -> None:
    """The list is a policy decision, so it is asserted rather than assumed."""
    assert Action.REFUND_APPROVE in ALWAYS_AUDITED
    assert Action.USER_MANAGE in ALWAYS_AUDITED
    assert Action.TRACE_VIEW in ALWAYS_AUDITED
    assert Action.ORDER_VIEW not in ALWAYS_AUDITED


def test_a_resource_is_described_by_its_attributes() -> None:
    text = describe(resources.Order(owner_customer_id=4, region=Region.NA))

    assert "Order" in text
    assert "owner_customer_id=4" in text
    assert "na" in text


def test_a_resource_with_no_attributes_still_describes() -> None:
    assert describe(resources.PolicyDocuments()) == "PolicyDocuments()"


def test_a_long_description_is_truncated_rather_than_rejected() -> None:
    """A too-long value must not lose the entry to a failed insert."""
    huge = resources.Proposal(
        ticket_owner_customer_id=1,
        region=Region.EU,
        amount_cents=1,
        owner_user_id=None,
    )
    text = describe(huge)

    assert len(text) <= MAX_RESOURCE
