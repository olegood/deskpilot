"""The payments Paywisp holds, and the rules for refunding them.

Pure logic: no MCP, no HTTP, no tokens. Whether a caller may refund is decided in
the server; whether a refund is *possible* is decided here, and that split keeps
the money rules testable on their own.

Amounts are integer minor units throughout, like Deskpilot's (D-017). The payments
are keyed by Acme Gear's order numbers, which is the only thing the two systems
share: the merchant told Paywisp its own reference when it took the payment.
"""

from __future__ import annotations

import hashlib
import json
import threading
import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum


class PaymentStatus(StrEnum):
    # Card authorised, money not yet taken. Nothing to refund: it would be voided.
    AUTHORIZED = "authorized"
    CAPTURED = "captured"
    PARTIALLY_REFUNDED = "partially_refunded"
    REFUNDED = "refunded"


class RefundError(Exception):
    """A refund that cannot happen. The message is safe to show a caller."""


@dataclass(frozen=True)
class Refund:
    refund_id: str
    amount_cents: int
    reason: str
    created_at: datetime


@dataclass(frozen=True)
class Payment:
    payment_id: str
    order_number: str
    amount_cents: int
    currency: str
    status: PaymentStatus
    card_brand: str
    card_last4: str
    created_at: datetime
    refunds: tuple[Refund, ...] = ()

    @property
    def refunded_cents(self) -> int:
        return sum(refund.amount_cents for refund in self.refunds)

    @property
    def refundable_cents(self) -> int:
        if self.status is PaymentStatus.AUTHORIZED:
            return 0
        return self.amount_cents - self.refunded_cents


@dataclass(frozen=True)
class IdempotentResult:
    """What a key was first used for, and what it produced."""

    fingerprint: str
    refund: Refund


@dataclass
class PaymentStore:
    """In memory, like ShipTrack's parcels. The interesting part is the wire."""

    payments: dict[str, Payment]
    _idempotency: dict[str, IdempotentResult] = field(default_factory=dict)
    # One lock around check-then-write. Two concurrent refunds reading the same
    # refundable amount and both succeeding is the classic double refund, and it
    # does not need a slow database to happen: two awaits in the wrong place will do.
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def get(self, order_number: str) -> Payment | None:
        return self.payments.get(order_number.strip().upper())

    def refund(
        self, order_number: str, amount_cents: int, reason: str, idempotency_key: str
    ) -> Refund:
        """Refund part or all of a payment, at most once per idempotency key.

        The same key with the same request returns the first refund again rather
        than refunding twice: a client that timed out and retried cannot tell
        whether the first attempt landed, and must be able to ask again safely.
        The same key with a *different* request is refused, because silently
        returning a refund for a different amount would be worse than an error.
        """
        key = order_number.strip().upper()
        fingerprint = _fingerprint(key, amount_cents, reason)
        with self._lock:
            previous = self._idempotency.get(idempotency_key)
            if previous is not None:
                if previous.fingerprint != fingerprint:
                    raise RefundError(
                        "That idempotency key was already used for a different refund."
                    )
                return previous.refund

            payment = self.payments.get(key)
            if payment is None:
                raise RefundError("There is no payment for that order.")
            if amount_cents <= 0:
                raise RefundError("A refund must be for a positive amount.")
            if payment.status is PaymentStatus.AUTHORIZED:
                raise RefundError("That payment was never captured, so there is nothing to refund.")
            if amount_cents > payment.refundable_cents:
                raise RefundError(
                    f"At most {payment.refundable_cents} can be refunded on that payment."
                )

            refund = Refund(
                refund_id=f"re_{uuid.uuid4().hex[:16]}",
                amount_cents=amount_cents,
                reason=reason,
                created_at=datetime.now(UTC),
            )
            refunds = (*payment.refunds, refund)
            refunded = sum(item.amount_cents for item in refunds)
            status = (
                PaymentStatus.REFUNDED
                if refunded == payment.amount_cents
                else PaymentStatus.PARTIALLY_REFUNDED
            )
            self.payments[key] = replace(payment, refunds=refunds, status=status)
            self._idempotency[idempotency_key] = IdempotentResult(fingerprint, refund)
            return refund


def _fingerprint(order_number: str, amount_cents: int, reason: str) -> str:
    canonical = json.dumps([order_number, amount_cents, reason], separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _at(month: int, day: int) -> datetime:
    return datetime(2026, month, day, 10, 30, tzinfo=UTC)


def seeded_store() -> PaymentStore:
    """Acme Gear's payments, matching the orders in Deskpilot's seed data.

    Totals and currencies agree with the seed, and a test on the Deskpilot side
    checks that they keep agreeing. The states are chosen to be worth asking about:
    ORD-1031 was cancelled and refunded in full, ORD-1017 has had a partial
    goodwill refund, and ORD-1050 is still only authorised.
    """

    def payment(
        number: str,
        cents: int,
        currency: str,
        month: int,
        day: int,
        brand: str,
        last4: str,
        status: PaymentStatus = PaymentStatus.CAPTURED,
        refunds: tuple[Refund, ...] = (),
    ) -> Payment:
        return Payment(
            payment_id=f"pay_{number.lower().replace('-', '')}",
            order_number=number,
            amount_cents=cents,
            currency=currency,
            status=status,
            card_brand=brand,
            card_last4=last4,
            created_at=_at(month, day),
            refunds=refunds,
        )

    payments = [
        payment("ORD-1001", 43900, "EUR", 8, 2, "visa", "4242"),
        payment("ORD-1002", 12900, "EUR", 9, 8, "visa", "4242"),
        payment(
            "ORD-1017",
            18900,
            "EUR",
            7,
            20,
            "mastercard",
            "5454",
            PaymentStatus.PARTIALLY_REFUNDED,
            (Refund("re_seed_1017", 5000, "Goodwill for a late delivery", _at(7, 30)),),
        ),
        payment("ORD-1023", 28800, "USD", 9, 3, "amex", "0005"),
        payment(
            "ORD-1031",
            8900,
            "USD",
            8,
            25,
            "visa",
            "1881",
            PaymentStatus.REFUNDED,
            (Refund("re_seed_1031", 8900, "Order cancelled before dispatch", _at(8, 26)),),
        ),
        payment("ORD-1042", 17700, "USD", 9, 5, "visa", "1881"),
        payment("ORD-1050", 4800, "EUR", 9, 10, "mastercard", "4444", PaymentStatus.AUTHORIZED),
        payment("ORD-1063", 34900, "USD", 8, 15, "visa", "0077"),
        payment("ORD-1077", 4500, "USD", 8, 20, "mastercard", "2222"),
        payment("ORD-1088", 18900, "USD", 9, 9, "visa", "9424"),
        payment("ORD-1095", 17800, "USD", 8, 28, "amex", "0005"),
        payment("ORD-1101", 6900, "EUR", 9, 7, "mastercard", "5454"),
    ]
    return PaymentStore(payments={item.order_number: item for item in payments})
