"""The carrier client, against the real ShipTrack service.

A contract test. ShipTrack is a dev-only dependency so both halves can run in one
process; nothing under `src/deskpilot` imports it, and a test asserts that. The
point is that the client is checked against something that can disagree with it
rather than against a mock written by the same person on the same day.

Run with: uv run pytest -m integration
"""

import asyncio
import re
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from pydantic import AnyHttpUrl
from shiptrack.app import create_app
from shiptrack.config import ChaosSettings
from shiptrack.config import Settings as CarrierSettings

from deskpilot.config import ShipTrackSettings
from deskpilot.integrations.shiptrack import CarrierError, CarrierUnavailable, ShipTrackClient
from deskpilot.integrations.shiptrack.breaker import State

pytestmark = pytest.mark.integration

SECRET = "a-shared-secret-for-tests"
PARCEL = "ST-100042"


def client_settings(**overrides: object) -> ShipTrackSettings:
    defaults: dict[str, object] = {
        "secret": SECRET,
        "retries": 2,
        "backoff_seconds": 0.01,
        "read_timeout_s": 0.5,
        "breaker_threshold": 2,
        "breaker_reset_seconds": 0.05,
    }
    if isinstance(overrides.get("base_url"), str):
        overrides["base_url"] = AnyHttpUrl(str(overrides["base_url"]))
    return ShipTrackSettings(**{**defaults, **overrides})  # type: ignore[arg-type]


@pytest.fixture
async def carrier() -> AsyncIterator[ShipTrackClient]:
    settings = CarrierSettings(_env_file=None, secret=SECRET)  # type: ignore[call-arg]
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with ShipTrackClient(client_settings(), transport=transport) as client:
            yield client


async def chaotic_client(**chaos: float) -> tuple[ShipTrackClient, object]:
    settings = CarrierSettings(_env_file=None, secret=SECRET)  # type: ignore[call-arg]
    app = create_app(settings.model_copy(update={"chaos": ChaosSettings(**chaos)}))
    context = app.router.lifespan_context(app)
    await context.__aenter__()
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return ShipTrackClient(client_settings(), transport=transport), context


# ── the happy path ──────────────────────────────────────────────────────────


async def test_the_client_signs_a_request_the_carrier_accepts(
    carrier: ShipTrackClient,
) -> None:
    """The contract test. Two independent implementations of one scheme agreeing."""
    shipment = await carrier.track(PARCEL)

    assert shipment is not None
    assert shipment["tracking_number"] == PARCEL
    assert shipment["status"] == "in_transit"


async def test_an_unknown_parcel_is_none_rather_than_an_error(
    carrier: ShipTrackClient,
) -> None:
    assert await carrier.track("ST-999999") is None


async def test_the_tracking_number_is_normalised(carrier: ShipTrackClient) -> None:
    assert await carrier.track("  st-100042 ") is not None


async def test_two_calls_both_succeed(carrier: ShipTrackClient) -> None:
    """Each request signs fresh, so the carrier's replay check does not bite."""
    assert await carrier.track(PARCEL) is not None
    assert await carrier.track(PARCEL) is not None


# ── the wrong credential ────────────────────────────────────────────────────


async def test_a_wrong_secret_is_an_error_and_is_not_retried() -> None:
    """Retrying a 401 is asking the same question and expecting a different answer."""
    settings = CarrierSettings(_env_file=None, secret=SECRET)  # type: ignore[call-arg]
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with ShipTrackClient(
            client_settings(secret="not the shared secret"), transport=transport
        ) as client:
            with pytest.raises(CarrierError) as caught:
                await client.track(PARCEL)
            assert not isinstance(caught.value, CarrierUnavailable)
            # A credential problem is ours to fix, so it must not trip the breaker
            # and stop every later request.
            assert client.breaker.state is State.CLOSED


async def test_no_secret_at_all_fails_when_the_client_is_built() -> None:
    with pytest.raises(CarrierError, match="SECRET"):
        ShipTrackClient(ShipTrackSettings())


# ── failures worth surviving ────────────────────────────────────────────────


async def test_a_failing_carrier_is_retried_and_then_reported() -> None:
    client, context = await chaotic_client(error_rate=1.0)
    try:
        with pytest.raises(CarrierUnavailable):
            await client.track(PARCEL)
    finally:
        await client.aclose()
        await context.__aexit__(None, None, None)  # type: ignore[attr-defined]


async def test_a_hang_becomes_a_failure_rather_than_a_wait() -> None:
    """The important one, and the one that needs a real socket.

    A 500 is obvious. A connection that stays open looks like a slow response,
    and a client without a read timeout waits for ever.

    This cannot be tested through ASGITransport. That transport calls the app
    directly in the same process, and an httpx timeout is a network timeout -
    there is no socket to give up on, so a hang in the handler simply hangs.
    Hence a real listener on a real port that accepts and never answers, which is
    exactly what the client has to survive.
    """

    # Released on teardown. A plain sleep would work just as well for the client,
    # but closing the server waits for its handlers, so the test would pay the
    # whole sleep on the way out.
    stop = asyncio.Event()

    async def accept_and_say_nothing(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        await reader.read(65536)
        await stop.wait()
        writer.close()

    server = await asyncio.start_server(accept_and_say_nothing, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        settings = client_settings(base_url=f"http://127.0.0.1:{port}")
        async with ShipTrackClient(settings) as client:
            # Three attempts at half a second, plus backoff. If this ever takes
            # thirty seconds, the read timeout has stopped working.
            async with asyncio.timeout(10):
                with pytest.raises(CarrierUnavailable):
                    await client.track(PARCEL)
            # One logical call, so one failure against the breaker however many
            # attempts it made. Counting each retry would trip the circuit on the
            # first bad request, which is the opposite of what retries are for.
            assert client.breaker.state is State.CLOSED
    finally:
        stop.set()
        server.close()
        await server.wait_closed()


async def test_repeated_failures_open_the_circuit() -> None:
    client, context = await chaotic_client(error_rate=1.0)
    try:
        for _ in range(client.settings.breaker_threshold):
            with pytest.raises(CarrierUnavailable):
                await client.track(PARCEL)

        assert client.breaker.state is State.OPEN
    finally:
        await client.aclose()
        await context.__aexit__(None, None, None)  # type: ignore[attr-defined]


async def test_an_open_circuit_fails_without_asking() -> None:
    """The point of the breaker: no timeout budget spent on a service that is down."""
    client, context = await chaotic_client(error_rate=1.0)
    try:
        for _ in range(client.settings.breaker_threshold):
            with pytest.raises(CarrierUnavailable):
                await client.track(PARCEL)

        async with asyncio.timeout(0.2):
            with pytest.raises(CarrierUnavailable):
                await client.track(PARCEL)
    finally:
        await client.aclose()
        await context.__aexit__(None, None, None)  # type: ignore[attr-defined]


async def test_the_circuit_closes_again_when_the_carrier_recovers(
    carrier: ShipTrackClient,
) -> None:
    carrier.breaker.record_failure()
    carrier.breaker.record_failure()
    assert not carrier.breaker.allows()

    await asyncio.sleep(carrier.settings.breaker_reset_seconds + 0.01)
    shipment = await carrier.track(PARCEL)

    assert shipment is not None
    assert carrier.breaker.state is State.CLOSED


# ── separation ──────────────────────────────────────────────────────────────


def test_nothing_in_deskpilot_imports_the_carrier_package() -> None:
    """The vendor is a test dependency, and must stay one.

    An import from `src` would mean the integration was really a function call in
    a costume, and every test of it would be testing the author's belief about
    the carrier rather than the carrier.
    """
    source = Path(__file__).resolve().parents[2] / "src" / "deskpilot"
    offenders = [
        path.relative_to(source)
        for path in source.rglob("*.py")
        if re.search(r"^\s*(import|from)\s+shiptrack", path.read_text(), re.MULTILINE)
    ]

    assert offenders == []
