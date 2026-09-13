"""Unit tests for the seed data. No database needed."""

from deskpilot.db.models import OrderStatus, Region
from deskpilot.db.seed import CUSTOMERS, ORDERS, validate_seed_data


def test_seed_data_is_consistent() -> None:
    validate_seed_data()


def test_reference_order_for_examples_exists() -> None:
    order = next(o for o in ORDERS if o.number == "ORD-1042")
    assert order.customer_email == "noah.kim@example.com"
    assert order.status is OrderStatus.SHIPPED
    assert order.tracking_number is not None


def test_every_region_and_status_is_represented() -> None:
    assert {c.region for c in CUSTOMERS} == set(Region)
    assert {o.status for o in ORDERS} == set(OrderStatus)


def test_seed_emails_use_reserved_domain() -> None:
    assert all(c.email.endswith("@example.com") for c in CUSTOMERS)
