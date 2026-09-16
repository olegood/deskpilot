"""The parcels ShipTrack knows about.

In memory and deterministic. A real carrier has a database; this one has a dict,
because the interesting part of the integration is the wire, not the storage.

The tracking numbers match the ones Acme Gear's seed data puts on its orders. That
is the only thing the two systems share, and it is shared the way it would be in
life: one of them wrote it on a parcel and told the other.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum


class Status(StrEnum):
    LABEL_CREATED = "label_created"
    IN_TRANSIT = "in_transit"
    OUT_FOR_DELIVERY = "out_for_delivery"
    DELIVERED = "delivered"
    # Reported delivered, disputed by the customer. A real state, and the one that
    # causes the most support tickets.
    DELIVERED_NOT_RECEIVED = "delivered_not_received"
    EXCEPTION = "exception"


@dataclass(frozen=True)
class Scan:
    """One event in a parcel's history."""

    at: datetime
    location: str
    description: str


@dataclass(frozen=True)
class Shipment:
    tracking_number: str
    status: Status
    destination: str
    expected_delivery: datetime | None
    scans: tuple[Scan, ...] = field(default_factory=tuple)

    @property
    def last_scan_at(self) -> datetime | None:
        return self.scans[-1].at if self.scans else None


def at(days_ago: int, hour: int = 9) -> datetime:
    """A time relative to now, so the data does not rot into the past."""
    return (datetime.now(UTC) - timedelta(days=days_ago)).replace(
        hour=hour, minute=0, second=0, microsecond=0
    )


def journey(tracking: str, days: int, destination: str, final: Status) -> Shipment:
    """A plausible history for a parcel that is progressing normally."""
    scans = [
        Scan(at(days, 8), "Bristol depot", "Label created"),
        Scan(at(days - 1, 21), "Bristol depot", "Collected"),
        Scan(at(days - 2, 4), "Midlands hub", "In transit"),
    ]
    if final in {Status.OUT_FOR_DELIVERY, Status.DELIVERED, Status.DELIVERED_NOT_RECEIVED}:
        scans.append(Scan(at(0, 7), destination, "Out for delivery"))
    if final in {Status.DELIVERED, Status.DELIVERED_NOT_RECEIVED}:
        scans.append(Scan(at(0, 11), destination, "Delivered, left in porch"))
    return Shipment(
        tracking_number=tracking,
        status=final,
        destination=destination,
        expected_delivery=at(-1, 17) if final is Status.IN_TRANSIT else None,
        scans=tuple(scans),
    )


def stalled(tracking: str, days_since_scan: int, destination: str) -> Shipment:
    """A parcel that stopped moving. The reason most tickets get opened."""
    return Shipment(
        tracking_number=tracking,
        status=Status.IN_TRANSIT,
        destination=destination,
        expected_delivery=at(days_since_scan - 3, 17),
        scans=(
            Scan(at(days_since_scan + 2, 8), "Bristol depot", "Label created"),
            Scan(at(days_since_scan + 1, 21), "Bristol depot", "Collected"),
            Scan(at(days_since_scan, 4), "Midlands hub", "In transit"),
        ),
    )


SHIPMENTS: dict[str, Shipment] = {
    shipment.tracking_number: shipment
    for shipment in (
        journey("ST-100001", 12, "Valencia", Status.DELIVERED),
        journey("ST-100017", 26, "Cork", Status.DELIVERED),
        journey("ST-100023", 8, "Denver", Status.OUT_FOR_DELIVERY),
        journey("ST-100042", 6, "Seattle", Status.IN_TRANSIT),
        journey("ST-100063", 17, "Osaka", Status.DELIVERED),
        # Scanned as delivered, and the customer says otherwise.
        journey("ST-100095", 14, "Denver", Status.DELIVERED_NOT_RECEIVED),
        # Eleven days without a scan: lost, by Acme Gear's own shipping policy.
        stalled("ST-100077", 11, "Portland"),
        stalled("ST-100101", 4, "Cork"),
    )
}
