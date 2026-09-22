"""Paywisp's payments and Deskpilot's orders describe the same shop.

They live in two services and neither reads the other's data, so nothing but this
test stops them drifting apart: an order whose payment is for a different amount
would make every refund conversation about it wrong in a way no other test sees.

Paywisp is a dev-only dependency of the backend, like ShipTrack, and for the same
reason: so tests can run the real vendor. The last test keeps it that way.
"""

import re
from pathlib import Path

from paywisp.mcp_server.payments import PaymentStatus, seeded_store

from deskpilot.db.models import OrderStatus
from deskpilot.db.seed import CURRENCY_BY_REGION, CUSTOMERS, ORDERS, PRODUCTS

PRICES = {product.sku: product.price_cents for product in PRODUCTS}
REGIONS = {customer.email: customer.region for customer in CUSTOMERS}

# What each order status means for its payment. A pending order has been
# authorised but not captured; a cancelled one was paid and then refunded in full.
PAYMENT_STATUSES = {
    OrderStatus.PENDING: {PaymentStatus.AUTHORIZED},
    OrderStatus.PAID: {PaymentStatus.CAPTURED},
    OrderStatus.SHIPPED: {PaymentStatus.CAPTURED, PaymentStatus.PARTIALLY_REFUNDED},
    OrderStatus.DELIVERED: {PaymentStatus.CAPTURED, PaymentStatus.PARTIALLY_REFUNDED},
    OrderStatus.CANCELLED: {PaymentStatus.REFUNDED},
}


def test_every_order_has_a_payment_and_every_payment_an_order() -> None:
    assert set(seeded_store().payments) == {order.number for order in ORDERS}


def test_each_payment_is_for_what_the_order_cost() -> None:
    payments = seeded_store().payments
    for order in ORDERS:
        payment = payments[order.number]
        total = sum(PRICES[sku] * quantity for sku, quantity in order.items)
        assert payment.amount_cents == total, order.number
        assert payment.currency == CURRENCY_BY_REGION[REGIONS[order.customer_email]], order.number


def test_each_payment_status_fits_its_order() -> None:
    payments = seeded_store().payments
    for order in ORDERS:
        assert payments[order.number].status in PAYMENT_STATUSES[order.status], order.number


def test_nothing_in_deskpilot_imports_the_payment_package() -> None:
    """Deskpilot talks to Paywisp over MCP and nothing else. An import from `src`
    would be a way round every token and scope check this milestone builds."""
    source = Path(__file__).resolve().parents[2] / "src" / "deskpilot"
    offenders = [
        path.relative_to(source)
        for path in source.rglob("*.py")
        if re.search(r"^\s*(import|from)\s+paywisp", path.read_text(), re.MULTILINE)
    ]

    assert offenders == []
