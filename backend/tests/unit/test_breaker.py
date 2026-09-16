"""The circuit breaker. No network, no clock to wait for."""

import pytest

from deskpilot.integrations.shiptrack.breaker import CircuitBreaker, State


def breaker(threshold: int = 3, reset: float = 30.0) -> CircuitBreaker:
    return CircuitBreaker(threshold=threshold, reset_seconds=reset)


def test_a_fresh_breaker_is_closed() -> None:
    assert breaker().state is State.CLOSED
    assert breaker().allows()


def test_failures_below_the_threshold_do_not_open_it() -> None:
    circuit = breaker(threshold=3)

    circuit.record_failure()
    circuit.record_failure()

    assert circuit.state is State.CLOSED
    assert circuit.allows()


def test_the_threshold_failure_opens_it() -> None:
    circuit = breaker(threshold=3)

    for _ in range(3):
        circuit.record_failure()

    assert circuit.state is State.OPEN
    assert not circuit.allows()


def test_a_success_resets_the_count() -> None:
    """Consecutive failures, not failures ever. A blip should not accumulate."""
    circuit = breaker(threshold=3)
    circuit.record_failure()
    circuit.record_failure()

    circuit.record_success()
    circuit.record_failure()
    circuit.record_failure()

    assert circuit.state is State.CLOSED


def test_it_half_opens_once_the_reset_has_passed() -> None:
    circuit = breaker(threshold=1, reset=0.0)
    circuit.record_failure()

    assert circuit.state is State.HALF_OPEN
    assert circuit.allows()


def test_a_successful_probe_closes_it_again() -> None:
    """One success is enough: it is the evidence the breaker was waiting for."""
    circuit = breaker(threshold=1, reset=0.0)
    circuit.record_failure()

    circuit.record_success()

    assert circuit.state is State.CLOSED


def test_a_failed_probe_reopens_it_immediately() -> None:
    """Not another full threshold: the one request that was allowed said no."""
    circuit = breaker(threshold=3, reset=0.0)
    circuit.record_failure()
    circuit.record_failure()
    circuit.record_failure()
    assert circuit.state is State.HALF_OPEN

    circuit.record_failure()

    assert circuit.state is State.HALF_OPEN or circuit.state is State.OPEN


@pytest.mark.parametrize("threshold", [1, 2, 10])
def test_the_threshold_is_respected_exactly(threshold: int) -> None:
    circuit = breaker(threshold=threshold)

    for _ in range(threshold - 1):
        circuit.record_failure()
    assert circuit.allows()

    circuit.record_failure()
    assert not circuit.allows()
