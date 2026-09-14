"""Unit tests for how a customer account is rendered for the model. No database."""

from datetime import UTC, datetime

from deskpilot.db.models import Customer, CustomerTier, Region
from deskpilot.tools.customers import describe


def sample_customer() -> Customer:
    return Customer(
        email="noah.kim@example.com",
        full_name="Noah Kim",
        region=Region.NA,
        tier=CustomerTier.STANDARD,
        created_at=datetime(2025, 3, 14, 9, 30, tzinfo=UTC),
    )


def test_describe_includes_the_facts_policy_depends_on() -> None:
    text = describe(sample_customer(), order_count=2)

    assert "Name: Noah Kim" in text
    assert "Email: noah.kim@example.com" in text
    assert "Region: na" in text
    # The tier decides the return window, so it has to reach the model.
    assert "Membership tier: standard" in text
    assert "Customer since: 2025-03-14" in text
    assert "Orders placed: 2" in text


def test_describe_handles_a_customer_with_no_orders() -> None:
    assert "Orders placed: 0" in describe(sample_customer(), order_count=0)
