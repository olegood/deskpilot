"""Callbacks from vendors.

An endpoint here is reachable by anybody who can find it, so the signature is what
makes it trustworthy — not the path, not the origin, not a header the sender was
asked nicely to include.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from deskpilot.api.deps import AppSettings, DbSession, Sessions
from deskpilot.authz.actions import AuditEvent
from deskpilot.authz.audit import record_event
from deskpilot.db.models import Order, OrderStatus
from deskpilot.integrations.shiptrack.inbound import WebhookRejected, verify_callback

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])

PATH = "/api/webhooks/shiptrack"

# What a carrier status means for an order. Anything not listed leaves the order
# alone: the carrier is authoritative about parcels, not about orders, and it has
# no business cancelling one.
ORDER_STATUS_FOR = {
    "delivered": OrderStatus.DELIVERED,
    "out_for_delivery": OrderStatus.SHIPPED,
    "in_transit": OrderStatus.SHIPPED,
}


class ShipmentEvent(BaseModel):
    event: str = Field(max_length=64)
    tracking_number: str = Field(max_length=64)
    status: str = Field(max_length=64)
    occurred_at: str | None = None


@router.post("/shiptrack", status_code=status.HTTP_204_NO_CONTENT)
async def shiptrack_callback(
    request: Request, db: DbSession, sessions: Sessions, settings: AppSettings
) -> None:
    """A parcel moved.

    Verified before the body is parsed as anything meaningful. Handing unverified
    bytes to a model, or to a database write, is the whole problem with webhooks.
    """
    carrier = settings.shiptrack
    if carrier.webhook_secret is None:
        # Not configured is not the same as wrong. Refuse, and say so in the log.
        logger.warning("a callback arrived but DESKPILOT_SHIPTRACK__WEBHOOK_SECRET is unset")
        raise WebhookRejected("no webhook secret is configured")

    body = await request.body()
    verify_callback(
        secret=carrier.webhook_secret.get_secret_value(),
        key_id=carrier.key_id,
        path=PATH,
        body=body,
        headers=dict(request.headers),
        nonces=request.app.state.webhook_nonces,
        max_skew_seconds=carrier.webhook_max_skew_seconds,
    )

    event = ShipmentEvent.model_validate_json(body)
    order = await db.scalar(
        select(Order).where(Order.tracking_number == event.tracking_number.strip().upper())
    )
    if order is None:
        # A parcel we do not have an order for. Not an error: the carrier serves
        # other customers, and a 4xx would make it retry something that will never
        # work.
        logger.info("callback for unknown tracking number %s", event.tracking_number)
        return

    new_status = ORDER_STATUS_FOR.get(event.status)
    changed = new_status is not None and order.status is not new_status
    if new_status is not None:
        order.status = new_status
    await db.commit()

    await record_event(
        sessions,
        AuditEvent.SHIPMENT_UPDATED,
        f"{event.tracking_number} is {event.status}"
        + (f"; {order.number} set to {new_status.value}" if changed and new_status else ""),
        resource=order.number,
    )
