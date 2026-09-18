"""Callbacks from the carrier, and everything the endpoint refuses.

The direction people forget. A team that signs its outbound requests carefully will
often accept a webhook because it arrived at a secret-looking URL, which is a
password sitting in every proxy log between there and here.

Run with: uv run pytest -m integration
"""

import hashlib
import hmac
import json
import time
import uuid
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deskpilot.api.app import create_app
from deskpilot.authz.audit import recent
from deskpilot.config import DatabaseSettings, Settings, ShipTrackSettings
from deskpilot.db.models import Order, OrderStatus

pytestmark = pytest.mark.integration

WEBHOOK_SECRET = "the-callback-secret"
PATH = "/api/webhooks/shiptrack"
# Noah's order ORD-1042, shipped, tracking ST-100042.
TRACKING = "ST-100042"


@pytest.fixture
async def client(
    test_database: DatabaseSettings, seeded_sessions: async_sessionmaker[AsyncSession]
) -> AsyncIterator[AsyncClient]:
    settings = Settings(_env_file=None, database=test_database)  # type: ignore[call-arg]
    settings = settings.model_copy(
        update={"shiptrack": ShipTrackSettings(secret="unused-here", webhook_secret=WEBHOOK_SECRET)}
    )
    app = create_app(settings)
    async with (
        AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client,
        app.router.lifespan_context(app),
    ):
        yield client


def event(status: str = "delivered", tracking: str = TRACKING) -> bytes:
    return json.dumps(
        {
            "event": "shipment.updated",
            "tracking_number": tracking,
            "status": status,
            "occurred_at": "2026-09-16T10:00:00+00:00",
        }
    ).encode()


def headers(
    body: bytes,
    secret: str = WEBHOOK_SECRET,
    key: str = "deskpilot",
    timestamp: str | None = None,
    nonce: str | None = None,
    path: str = PATH,
) -> dict[str, str]:
    timestamp = timestamp if timestamp is not None else str(int(time.time()))
    nonce = nonce if nonce is not None else uuid.uuid4().hex
    message = "\n".join(["POST", path, timestamp, nonce, hashlib.sha256(body).hexdigest()])
    return {
        "X-ShipTrack-Key": key,
        "X-ShipTrack-Timestamp": timestamp,
        "X-ShipTrack-Nonce": nonce,
        "X-ShipTrack-Signature": hmac.new(
            secret.encode(), message.encode(), hashlib.sha256
        ).hexdigest(),
        "Content-Type": "application/json",
    }


async def order_status(sessions: async_sessionmaker[AsyncSession], number: str) -> OrderStatus:
    async with sessions() as session:
        order = await session.scalar(select(Order).where(Order.number == number))
    assert order is not None
    return order.status


# ── the happy path ──────────────────────────────────────────────────────────


async def test_a_signed_callback_updates_the_order(
    client: AsyncClient, seeded_sessions: async_sessionmaker[AsyncSession]
) -> None:
    body = event("delivered")

    response = await client.post(PATH, content=body, headers=headers(body))

    assert response.status_code == 204
    assert await order_status(seeded_sessions, "ORD-1042") is OrderStatus.DELIVERED


async def test_a_callback_is_recorded(
    client: AsyncClient, seeded_sessions: async_sessionmaker[AsyncSession]
) -> None:
    """An order changing state with nobody asking is worth accounting for."""
    body = event("delivered")

    await client.post(PATH, content=body, headers=headers(body))

    async with seeded_sessions() as session:
        entries = await recent(session)
    assert entries
    assert entries[0].event == "shipment.updated"
    assert "ORD-1042" in (entries[0].resource or "")


async def test_a_status_the_carrier_has_no_business_setting_is_ignored(
    client: AsyncClient, seeded_sessions: async_sessionmaker[AsyncSession]
) -> None:
    """The carrier is authoritative about parcels, not about orders."""
    body = event("exception")

    response = await client.post(PATH, content=body, headers=headers(body))

    assert response.status_code == 204
    assert await order_status(seeded_sessions, "ORD-1042") is OrderStatus.SHIPPED


async def test_an_unknown_tracking_number_is_accepted_quietly(client: AsyncClient) -> None:
    """The carrier has other customers. A 4xx would make it retry for ever."""
    body = event(tracking="ST-999999")

    assert (await client.post(PATH, content=body, headers=headers(body))).status_code == 204


# ── what it refuses ─────────────────────────────────────────────────────────


async def test_an_unsigned_callback_is_refused(
    client: AsyncClient, seeded_sessions: async_sessionmaker[AsyncSession]
) -> None:
    """Reaching the URL is not authentication."""
    response = await client.post(
        PATH, content=event(), headers={"Content-Type": "application/json"}
    )

    assert response.status_code == 401
    assert await order_status(seeded_sessions, "ORD-1042") is OrderStatus.SHIPPED


async def test_a_wrong_secret_is_refused(client: AsyncClient) -> None:
    body = event()

    response = await client.post(PATH, content=body, headers=headers(body, secret="guessed"))

    assert response.status_code == 401


async def test_a_tampered_body_is_refused(
    client: AsyncClient, seeded_sessions: async_sessionmaker[AsyncSession]
) -> None:
    """The body digest is what stops this. Sign one event, deliver another."""
    signed = headers(event("in_transit"))

    response = await client.post(PATH, content=event("delivered"), headers=signed)

    assert response.status_code == 401
    assert await order_status(seeded_sessions, "ORD-1042") is OrderStatus.SHIPPED


async def test_a_replayed_callback_is_refused(client: AsyncClient) -> None:
    body = event()
    signed = headers(body)

    first = await client.post(PATH, content=body, headers=signed)
    second = await client.post(PATH, content=body, headers=signed)

    assert first.status_code == 204
    assert second.status_code == 401


async def test_an_old_callback_is_refused(client: AsyncClient) -> None:
    body = event()
    stale = str(int(time.time()) - 3600)

    response = await client.post(PATH, content=body, headers=headers(body, timestamp=stale))

    assert response.status_code == 401


async def test_an_unknown_key_id_is_refused(client: AsyncClient) -> None:
    body = event()

    response = await client.post(PATH, content=body, headers=headers(body, key="somebody"))

    assert response.status_code == 401


async def test_a_signature_for_another_path_is_refused(client: AsyncClient) -> None:
    body = event()

    response = await client.post(
        PATH, content=body, headers=headers(body, path="/api/webhooks/other")
    )

    assert response.status_code == 401


async def test_every_refusal_reads_the_same(client: AsyncClient) -> None:
    body = event()
    wrong = await client.post(PATH, content=body, headers=headers(body, secret="guessed"))
    unsigned = await client.post(PATH, content=body)

    assert wrong.json() == unsigned.json()


async def test_the_outbound_secret_does_not_work_for_callbacks(client: AsyncClient) -> None:
    """Two directions, two secrets. A leak of one must not buy the other."""
    body = event()

    response = await client.post(PATH, content=body, headers=headers(body, secret="unused-here"))

    assert response.status_code == 401
