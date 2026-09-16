"""The carrier's API, and everything it refuses."""

import time
import uuid

import pytest
from httpx import AsyncClient, Response
from tests.conftest import KEY_ID, SECRET

from shiptrack import signing
from shiptrack.config import ChaosSettings

PARCEL = "/api/shipments/ST-100042"


def headers(
    path: str = PARCEL,
    method: str = "GET",
    body: bytes = b"",
    secret: str = SECRET,
    key: str = KEY_ID,
    timestamp: str | None = None,
    nonce: str | None = None,
) -> dict[str, str]:
    timestamp = timestamp if timestamp is not None else str(int(time.time()))
    nonce = nonce if nonce is not None else uuid.uuid4().hex
    return {
        signing.HEADER_KEY: key,
        signing.HEADER_TIMESTAMP: timestamp,
        signing.HEADER_NONCE: nonce,
        signing.HEADER_SIGNATURE: signing.sign(secret, method, path, timestamp, nonce, body),
    }


async def fetch(client: AsyncClient, path: str = PARCEL, **kwargs: object) -> Response:
    return await client.get(path, headers=headers(path=path, **kwargs))  # type: ignore[arg-type]


# ── the happy path ──────────────────────────────────────────────────────────


async def test_a_signed_request_returns_a_shipment(client: AsyncClient) -> None:
    response = await fetch(client)

    assert response.status_code == 200
    body = response.json()
    assert body["tracking_number"] == "ST-100042"
    assert body["status"] == "in_transit"
    assert body["scans"]


async def test_a_parcel_scanned_as_delivered_but_disputed(client: AsyncClient) -> None:
    """The state that causes the most support tickets."""
    response = await fetch(client, path="/api/shipments/ST-100095")

    assert response.json()["status"] == "delivered_not_received"


async def test_a_stalled_parcel_reports_when_it_last_moved(client: AsyncClient) -> None:
    response = await fetch(client, path="/api/shipments/ST-100077")

    body = response.json()
    assert body["status"] == "in_transit"
    assert body["last_scan_at"]


async def test_an_unknown_tracking_number_is_404(client: AsyncClient) -> None:
    response = await fetch(client, path="/api/shipments/ST-999999")

    assert response.status_code == 404


async def test_health_needs_no_signature(client: AsyncClient) -> None:
    """A container healthcheck should not need a credential."""
    assert (await client.get("/api/health")).status_code == 200


# ── what it refuses ─────────────────────────────────────────────────────────


async def test_an_unsigned_request_is_refused(client: AsyncClient) -> None:
    response = await client.get(PARCEL)

    assert response.status_code == 401
    assert response.json()["detail"] == "The request could not be authenticated."


@pytest.mark.parametrize(
    "missing",
    list(
        (
            signing.HEADER_KEY,
            signing.HEADER_TIMESTAMP,
            signing.HEADER_NONCE,
            signing.HEADER_SIGNATURE,
        )
    ),
)
async def test_every_signing_header_is_required(client: AsyncClient, missing: str) -> None:
    sent = headers()
    del sent[missing]

    assert (await client.get(PARCEL, headers=sent)).status_code == 401


async def test_a_wrong_secret_is_refused(client: AsyncClient) -> None:
    assert (await fetch(client, secret="not the shared secret")).status_code == 401


async def test_an_unknown_key_id_is_refused(client: AsyncClient) -> None:
    assert (await fetch(client, key="somebody-else")).status_code == 401


async def test_every_refusal_reads_the_same(client: AsyncClient) -> None:
    """A different message per cause tells a prober which half they got right."""
    wrong_secret = await fetch(client, secret="wrong")
    unknown_key = await fetch(client, key="nobody")
    unsigned = await client.get(PARCEL)

    assert wrong_secret.json() == unknown_key.json() == unsigned.json()


# ── replay ──────────────────────────────────────────────────────────────────


async def test_an_old_request_is_refused(client: AsyncClient) -> None:
    """A capture stops working once it falls outside the window."""
    old = str(int(time.time()) - 3600)

    assert (await fetch(client, timestamp=old)).status_code == 401


async def test_a_request_from_the_future_is_refused(client: AsyncClient) -> None:
    """Allowing it would widen the replay window rather than narrow it."""
    ahead = str(int(time.time()) + 3600)

    assert (await fetch(client, timestamp=ahead)).status_code == 401


async def test_a_timestamp_that_is_not_a_number_is_refused(client: AsyncClient) -> None:
    assert (await fetch(client, timestamp="yesterday")).status_code == 401


async def test_the_same_request_cannot_be_replayed(client: AsyncClient) -> None:
    """The whole point of the nonce: a valid capture works exactly once."""
    sent = headers()

    first = await client.get(PARCEL, headers=sent)
    second = await client.get(PARCEL, headers=sent)

    assert first.status_code == 200
    assert second.status_code == 401


async def test_a_failed_request_does_not_burn_its_nonce(client: AsyncClient) -> None:
    """Otherwise anybody could spend a nonce they never legitimately used."""
    nonce = uuid.uuid4().hex

    refused = await fetch(client, nonce=nonce, secret="wrong")
    accepted = await fetch(client, nonce=nonce)

    assert refused.status_code == 401
    assert accepted.status_code == 200


async def test_a_signature_for_one_path_does_not_work_for_another(
    client: AsyncClient,
) -> None:
    """Without the path in the signature, one valid capture opens every parcel."""
    sent = headers(path=PARCEL)

    response = await client.get("/api/shipments/ST-100001", headers=sent)

    assert response.status_code == 401


# ── chaos ───────────────────────────────────────────────────────────────────


async def test_the_carrier_can_be_made_to_fail(chaotic: AsyncClient) -> None:
    response = await fetch(chaotic)

    assert response.status_code == 500
    assert response.json()["detail"] == "The carrier is unavailable."


async def test_chaos_does_not_change_how_a_bad_signature_is_refused(
    chaotic: AsyncClient,
) -> None:
    """Otherwise the failure rate becomes a way to tell whether a key was right."""
    response = await fetch(chaotic, secret="wrong")

    assert response.status_code == 401


async def test_chaos_can_be_changed_at_runtime(client: AsyncClient) -> None:
    body = ChaosSettings(error_rate=1.0).model_dump_json().encode()
    path = "/api/_chaos"

    changed = await client.put(
        path,
        content=body,
        headers={
            **headers(path=path, method="PUT", body=body),
            "Content-Type": "application/json",
        },
    )

    assert changed.status_code == 200
    assert (await fetch(client)).status_code == 500


async def test_changing_chaos_needs_a_signature(client: AsyncClient) -> None:
    response = await client.put("/api/_chaos", json={"error_rate": 1.0})

    assert response.status_code == 401
