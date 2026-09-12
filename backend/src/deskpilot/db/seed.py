"""Deterministic seed data for the Acme Gear shop.

The same data is loaded every time, so tests, demos, and evals can refer to known
customers and orders such as ORD-1042. Emails use the reserved example.com domain.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from deskpilot.db.models import (
    Customer,
    CustomerTier,
    Order,
    OrderItem,
    OrderStatus,
    Product,
    Region,
)

# Prices are the same number in every currency: a simplification for a fictional shop.
CURRENCY_BY_REGION = {Region.EU: "EUR", Region.NA: "USD", Region.APAC: "USD"}
STATUSES_WITH_TRACKING = {OrderStatus.SHIPPED, OrderStatus.DELIVERED}


@dataclass(frozen=True)
class ProductSeed:
    sku: str
    name: str
    price_cents: int


@dataclass(frozen=True)
class CustomerSeed:
    email: str
    full_name: str
    region: Region
    tier: CustomerTier


@dataclass(frozen=True)
class OrderSeed:
    number: str
    customer_email: str
    status: OrderStatus
    placed_at: datetime
    items: tuple[tuple[str, int], ...]  # (sku, quantity)
    tracking_number: str | None = None
    notes: str | None = None


def at(month: int, day: int) -> datetime:
    return datetime(2026, month, day, 10, 0, tzinfo=UTC)


PRODUCTS: tuple[ProductSeed, ...] = (
    ProductSeed("AG-PACK-35", "Trailhead 35L Backpack", 12_900),
    ProductSeed("AG-TENT-2P", "Ridgeline 2-Person Tent", 34_900),
    ProductSeed("AG-LAMP-400", "Beacon 400 Headlamp", 4_500),
    ProductSeed("AG-BAG-M5", "Summit -5°C Sleeping Bag", 21_900),
    ProductSeed("AG-STOVE", "Ember Camp Stove", 6_900),
    ProductSeed("AG-BOTTLE-1L", "Canyon 1L Bottle", 2_400),
    ProductSeed("AG-JACKET", "Squall Rain Jacket", 18_900),
    ProductSeed("AG-POLES", "Switchback Trekking Poles", 8_900),
)

CUSTOMERS: tuple[CustomerSeed, ...] = (
    CustomerSeed("ana.garcia@example.com", "Ana García", Region.EU, CustomerTier.GOLD),
    CustomerSeed("liam.oconnor@example.com", "Liam O'Connor", Region.EU, CustomerTier.STANDARD),
    CustomerSeed("maya.patel@example.com", "Maya Patel", Region.NA, CustomerTier.GOLD),
    CustomerSeed("noah.kim@example.com", "Noah Kim", Region.NA, CustomerTier.STANDARD),
    CustomerSeed("sofia.rossi@example.com", "Sofia Rossi", Region.EU, CustomerTier.STANDARD),
    CustomerSeed("kenji.tanaka@example.com", "Kenji Tanaka", Region.APAC, CustomerTier.GOLD),
    CustomerSeed("chloe.martin@example.com", "Chloe Martin", Region.NA, CustomerTier.STANDARD),
    CustomerSeed("aisha.rahman@example.com", "Aisha Rahman", Region.APAC, CustomerTier.STANDARD),
)

ORDERS: tuple[OrderSeed, ...] = (
    OrderSeed(
        "ORD-1001",
        "ana.garcia@example.com",
        OrderStatus.DELIVERED,
        at(8, 2),
        (("AG-TENT-2P", 1), ("AG-LAMP-400", 2)),
        tracking_number="ST-100001",
    ),
    OrderSeed(
        "ORD-1002",
        "ana.garcia@example.com",
        OrderStatus.PAID,
        at(9, 8),
        (("AG-PACK-35", 1),),
    ),
    OrderSeed(
        "ORD-1017",
        "liam.oconnor@example.com",
        OrderStatus.DELIVERED,
        at(7, 20),
        (("AG-JACKET", 1),),
        tracking_number="ST-100017",
    ),
    OrderSeed(
        "ORD-1023",
        "maya.patel@example.com",
        OrderStatus.SHIPPED,
        at(9, 3),
        (("AG-BAG-M5", 1), ("AG-STOVE", 1)),
        tracking_number="ST-100023",
    ),
    OrderSeed(
        "ORD-1031",
        "noah.kim@example.com",
        OrderStatus.CANCELLED,
        at(8, 25),
        (("AG-POLES", 1),),
    ),
    OrderSeed(
        "ORD-1042",
        "noah.kim@example.com",
        OrderStatus.SHIPPED,
        at(9, 5),
        (("AG-PACK-35", 1), ("AG-BOTTLE-1L", 2)),
        tracking_number="ST-100042",
    ),
    OrderSeed(
        "ORD-1050",
        "sofia.rossi@example.com",
        OrderStatus.PENDING,
        at(9, 10),
        (("AG-BOTTLE-1L", 2),),
    ),
    OrderSeed(
        "ORD-1063",
        "kenji.tanaka@example.com",
        OrderStatus.DELIVERED,
        at(8, 15),
        (("AG-TENT-2P", 1),),
        tracking_number="ST-100063",
    ),
    OrderSeed(
        "ORD-1077",
        "chloe.martin@example.com",
        OrderStatus.SHIPPED,
        at(8, 20),
        (("AG-LAMP-400", 1),),
        tracking_number="ST-100077",
    ),
    OrderSeed(
        "ORD-1088",
        "aisha.rahman@example.com",
        OrderStatus.PAID,
        at(9, 9),
        (("AG-JACKET", 1),),
        notes="Please leave the parcel at the back door.",
    ),
    OrderSeed(
        "ORD-1095",
        "maya.patel@example.com",
        OrderStatus.DELIVERED,
        at(8, 28),
        (("AG-POLES", 2),),
        tracking_number="ST-100095",
    ),
    OrderSeed(
        "ORD-1101",
        "liam.oconnor@example.com",
        OrderStatus.SHIPPED,
        at(9, 7),
        (("AG-STOVE", 1),),
        tracking_number="ST-100101",
    ),
)


class SeedError(Exception):
    """Raised when seeding can't proceed safely."""


@dataclass(frozen=True)
class SeedResult:
    customers: int
    products: int
    orders: int


def validate_seed_data() -> None:
    """Check the seed data is internally consistent. Raises SeedError if not."""
    errors: list[str] = []
    emails = [c.email for c in CUSTOMERS]
    skus = [p.sku for p in PRODUCTS]
    numbers = [o.number for o in ORDERS]
    for label, values in (("email", emails), ("sku", skus), ("order number", numbers)):
        duplicates = {v for v in values if values.count(v) > 1}
        if duplicates:
            errors.append(f"duplicate {label}: {sorted(duplicates)}")
    for order in ORDERS:
        if order.customer_email not in emails:
            errors.append(f"{order.number}: unknown customer {order.customer_email}")
        if not order.items:
            errors.append(f"{order.number}: no items")
        for sku, quantity in order.items:
            if sku not in skus:
                errors.append(f"{order.number}: unknown product {sku}")
            if quantity <= 0:
                errors.append(f"{order.number}: quantity must be positive")
        has_tracking = order.tracking_number is not None
        if has_tracking != (order.status in STATUSES_WITH_TRACKING):
            errors.append(f"{order.number}: tracking number doesn't match status {order.status}")
    if errors:
        raise SeedError("; ".join(errors))


async def seed(session: AsyncSession, *, reset: bool = False) -> SeedResult:
    """Load the seed data in one transaction.

    Without reset, refuses to touch a database that already has customers.
    With reset, deletes all shop data first.
    """
    validate_seed_data()
    async with session.begin():
        if reset:
            await session.execute(
                text("TRUNCATE order_items, orders, products, customers RESTART IDENTITY CASCADE")
            )
        else:
            existing = await session.scalar(select(func.count()).select_from(Customer))
            if existing:
                raise SeedError("the database already has shop data; use --reset to replace it")

        products = {
            p.sku: Product(sku=p.sku, name=p.name, price_cents=p.price_cents) for p in PRODUCTS
        }
        customers = {
            c.email: Customer(email=c.email, full_name=c.full_name, region=c.region, tier=c.tier)
            for c in CUSTOMERS
        }
        session.add_all([*products.values(), *customers.values()])

        for o in ORDERS:
            customer = customers[o.customer_email]
            session.add(
                Order(
                    number=o.number,
                    customer=customer,
                    status=o.status,
                    currency=CURRENCY_BY_REGION[customer.region],
                    placed_at=o.placed_at,
                    tracking_number=o.tracking_number,
                    notes=o.notes,
                    items=[
                        OrderItem(
                            product=products[sku],
                            quantity=quantity,
                            unit_price_cents=products[sku].price_cents,
                        )
                        for sku, quantity in o.items
                    ],
                )
            )
    return SeedResult(customers=len(CUSTOMERS), products=len(PRODUCTS), orders=len(ORDERS))
