"""Unit tests for model behavior that doesn't need a database."""

from deskpilot.db.models import Order, OrderItem


def test_order_total_is_sum_of_line_totals():
    order = Order(
        items=[
            OrderItem(quantity=1, unit_price_cents=12_900),
            OrderItem(quantity=2, unit_price_cents=2_400),
        ]
    )
    assert order.total_cents == 17_700


def test_line_total_multiplies_quantity_by_unit_price():
    assert OrderItem(quantity=3, unit_price_cents=4_500).line_total_cents == 13_500
