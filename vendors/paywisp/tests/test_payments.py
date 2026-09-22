"""The money rules on their own. No MCP, no tokens."""

import threading
import time
import types
import uuid

import pytest

from paywisp.mcp_server.payments import PaymentStatus, RefundError, seeded_store


def key() -> str:
    return uuid.uuid4().hex


def test_a_partial_refund_leaves_the_rest_refundable() -> None:
    store = seeded_store()

    store.refund("ORD-1042", 7700, "Part", key())

    payment = store.get("ORD-1042")
    assert payment is not None
    assert payment.status is PaymentStatus.PARTIALLY_REFUNDED
    assert payment.refundable_cents == 10000


def test_refunding_everything_marks_it_refunded() -> None:
    store = seeded_store()

    store.refund("ORD-1042", 17700, "All", key())

    payment = store.get("ORD-1042")
    assert payment is not None
    assert payment.status is PaymentStatus.REFUNDED
    assert payment.refundable_cents == 0


def test_a_refunded_payment_cannot_be_refunded_again() -> None:
    with pytest.raises(RefundError, match="At most 0"):
        seeded_store().refund("ORD-1031", 1, "Again", key())


def test_an_authorised_payment_cannot_be_refunded() -> None:
    """Nothing was taken, so there is nothing to give back. It would be voided."""
    with pytest.raises(RefundError, match="never captured"):
        seeded_store().refund("ORD-1050", 100, "x", key())


def test_order_numbers_are_normalised() -> None:
    assert seeded_store().get("  ord-1042 ") is not None


def test_concurrent_refunds_cannot_both_take_the_last_of_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The classic double refund: two requests read the same balance and both succeed.

    Without help the window between the check and the write is too short to hit,
    and the test would pass with the lock removed. Making the refund id slow to
    generate, which happens inside that window, holds it open.
    """

    def slow_uuid4() -> uuid.UUID:
        time.sleep(0.01)
        return uuid.uuid4()

    monkeypatch.setattr("paywisp.mcp_server.payments.uuid", types.SimpleNamespace(uuid4=slow_uuid4))
    store = seeded_store()
    outcomes: list[str] = []
    start = threading.Barrier(8)

    def attempt() -> None:
        start.wait()
        try:
            store.refund("ORD-1042", 17700, "All of it", key())
            outcomes.append("refunded")
        except RefundError:
            outcomes.append("refused")

    threads = [threading.Thread(target=attempt) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert outcomes.count("refunded") == 1
    payment = store.get("ORD-1042")
    assert payment is not None
    assert payment.refunded_cents == 17700


def test_the_seed_is_self_consistent() -> None:
    for payment in seeded_store().payments.values():
        assert payment.refunded_cents <= payment.amount_cents
        assert len(payment.card_last4) == 4
        if payment.status is PaymentStatus.REFUNDED:
            assert payment.refundable_cents == 0
