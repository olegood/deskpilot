"""The ShipTrack HTTP API."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import APIRouter, Depends, FastAPI, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from shiptrack.auth import REJECTED, AuthFailed, NonceStore, verify
from shiptrack.chaos import ChaosMonkey
from shiptrack.config import ChaosSettings, Settings
from shiptrack.data import SHIPMENTS, Shipment, Status

logger = logging.getLogger(__name__)
router = APIRouter()


class ScanOut(BaseModel):
    at: datetime
    location: str
    description: str


class ShipmentOut(BaseModel):
    tracking_number: str
    status: Status
    destination: str
    expected_delivery: datetime | None
    last_scan_at: datetime | None
    scans: list[ScanOut]


def to_out(shipment: Shipment) -> ShipmentOut:
    return ShipmentOut(
        tracking_number=shipment.tracking_number,
        status=shipment.status,
        destination=shipment.destination,
        expected_delivery=shipment.expected_delivery,
        last_scan_at=shipment.last_scan_at,
        scans=[
            ScanOut(at=scan.at, location=scan.location, description=scan.description)
            for scan in shipment.scans
        ],
    )


async def authenticated(request: Request) -> None:
    """Every real endpoint depends on this."""
    await verify(request, request.app.state.settings, request.app.state.nonces)


async def misbehave(request: Request) -> None:
    """Applied before the handler, so chaos affects authenticated requests too.

    Ordered after authentication on purpose: an unauthenticated request should be
    refused quickly and consistently, or the chaos settings become a way to tell
    whether a key was right.
    """
    monkey: ChaosMonkey = request.app.state.chaos
    await monkey.delay()
    if monkey.should_hang():
        await monkey.hang()
    if monkey.should_fail():
        raise RuntimeError("chaos: the carrier is having a bad day")


@router.get("/api/shipments/{tracking_number}", response_model=ShipmentOut)
async def read_shipment(tracking_number: str) -> ShipmentOut:
    """Where a parcel is, and everywhere it has been."""
    shipment = SHIPMENTS.get(tracking_number.strip().upper())
    if shipment is None:
        return JSONResponse(  # type: ignore[return-value]
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": "No shipment with that tracking number."},
        )
    return to_out(shipment)


@router.put("/api/_chaos", response_model=ChaosSettings)
async def set_chaos(request: Request, settings: ChaosSettings) -> ChaosSettings:
    """Change how badly the carrier behaves, at runtime.

    Signed like everything else. A test or a developer can turn a hang on for one
    request and off again, without restarting anything.
    """
    request.app.state.chaos = ChaosMonkey(settings)
    logger.warning("chaos settings changed to %s", settings)
    return settings


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = settings
        app.state.nonces = NonceStore(settings.nonce_capacity)
        app.state.chaos = ChaosMonkey(settings.chaos)
        logger.info("shiptrack ready with %d shipments", len(SHIPMENTS))
        yield

    app = FastAPI(title="ShipTrack", summary="A fictional carrier.", lifespan=lifespan)

    @app.exception_handler(AuthFailed)
    async def _auth_failed(request: Request, exc: AuthFailed) -> JSONResponse:
        # The real reason goes to the log. The caller gets one message, so a
        # probe cannot learn which half of the credential was right.
        logger.info("rejected a request: %s", exc.reason)
        return JSONResponse(status_code=status.HTTP_401_UNAUTHORIZED, content={"detail": REJECTED})

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("shiptrack failed on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "The carrier is unavailable."},
        )

    app.include_router(router, dependencies=[Depends(authenticated), Depends(misbehave)])

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        """Unsigned, so a container healthcheck does not need a credential."""
        return {"status": "ok"}

    return app
