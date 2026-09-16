"""Asking the carrier about a customer's parcel."""

from __future__ import annotations

import logging

from langchain_core.tools import tool
from langgraph.prebuilt import ToolRuntime
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from deskpilot.authz import resources
from deskpilot.authz.actions import Action
from deskpilot.authz.audit import guard
from deskpilot.authz.engine import Forbidden
from deskpilot.db.models import Order
from deskpilot.graph.context import AgentContext
from deskpilot.integrations.shiptrack.errors import CarrierError, CarrierUnavailable

logger = logging.getLogger(__name__)

NOT_FOUND = "No order with that number was found for this customer."
NOT_SHIPPED = "That order has not been dispatched yet, so there is nothing to track."
NO_RECORD = "The carrier has no record of that parcel yet. It can take a day to appear."
UNAVAILABLE = (
    "The carrier is not responding at the moment. Tell the customer the tracking "
    "information is temporarily unavailable and that they should try again shortly."
)


def describe(shipment: dict[str, object]) -> str:
    """Render what the carrier said, for the model."""
    lines = [
        f"Carrier status: {shipment.get('status')}",
        f"Destination: {shipment.get('destination')}",
    ]
    if shipment.get("expected_delivery"):
        lines.append(f"Expected delivery: {shipment['expected_delivery']}")
    if shipment.get("last_scan_at"):
        lines.append(f"Last scanned: {shipment['last_scan_at']}")
    scans = shipment.get("scans")
    if isinstance(scans, list) and scans:
        lines.append("Recent scans, newest last:")
        # The last few only. A parcel can have dozens, and the old ones say
        # nothing the status does not.
        lines.extend(
            f"- {scan.get('at')}  {scan.get('location')}: {scan.get('description')}"
            for scan in scans[-5:]
            if isinstance(scan, dict)
        )
    return "\n".join(lines)


@tool
async def track_shipment(order_number: str, runtime: ToolRuntime[AgentContext]) -> str:
    """Ask the carrier where one of this customer's orders is.

    Takes an order number, not a tracking number, and reports the carrier's own
    status and recent scans. Use it when the customer asks where a parcel is or
    why it has not arrived.
    """
    context = runtime.context
    if context.carrier is None:
        logger.warning("track_shipment called with no carrier configured")
        return UNAVAILABLE

    async with context.session_factory() as session:
        order = await session.scalar(
            select(Order)
            .where(Order.number == order_number.strip().upper())
            .options(selectinload(Order.customer))
        )
    if order is None:
        return NOT_FOUND

    # The tracking number is looked up from an order the customer owns, never
    # taken from the model. Otherwise the tool would report on any parcel in the
    # carrier's system to anybody who could guess a number.
    try:
        await guard(
            context.session_factory,
            context.principal,
            Action.ORDER_VIEW,
            resources.Order(owner_customer_id=order.customer_id, region=order.customer.region),
        )
    except Forbidden:
        return NOT_FOUND

    if not order.tracking_number:
        return NOT_SHIPPED

    try:
        shipment = await context.carrier.track(order.tracking_number)
    except CarrierUnavailable:
        # Deliberately not an exception the model has to interpret. The agent's
        # job here is to say something true to the customer, and "we cannot reach
        # the carrier" is true.
        return UNAVAILABLE
    except CarrierError:
        logger.exception("the carrier refused a request")
        return UNAVAILABLE

    if shipment is None:
        return NO_RECORD
    return f"Order {order.number}, tracking {order.tracking_number}\n{describe(shipment)}"
