"""SQLAlchemy models for the Acme Gear shop.

Conventions:
- Money is stored as integer cents, never as floats.
- Timestamps are timezone-aware.
- Enums are stored as strings with a CHECK constraint instead of native PostgreSQL
  enums, which are painful to change in migrations.
- Relationships use lazy="raise": with async sessions an implicit lazy load would
  fail at runtime, so every query must load related rows explicitly.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, MetaData, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Deterministic constraint names, so Alembic migrations can refer to them reliably.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def str_enum(enum_class: type[StrEnum], name: str) -> Enum:
    """Store a StrEnum by value as VARCHAR with a CHECK constraint."""
    return Enum(
        enum_class,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=32,
        values_callable=lambda members: [member.value for member in members],
    )


class Region(StrEnum):
    EU = "eu"
    NA = "na"
    APAC = "apac"


class CustomerTier(StrEnum):
    STANDARD = "standard"
    GOLD = "gold"


class OrderStatus(StrEnum):
    PENDING = "pending"
    PAID = "paid"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    full_name: Mapped[str] = mapped_column(String(200))
    region: Mapped[Region] = mapped_column(str_enum(Region, "region"))
    tier: Mapped[CustomerTier] = mapped_column(str_enum(CustomerTier, "customer_tier"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    orders: Mapped[list[Order]] = relationship(back_populates="customer", lazy="raise")


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (CheckConstraint("price_cents >= 0", name="price_not_negative"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    sku: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    price_cents: Mapped[int]


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Public reference that customers see and type, e.g. ORD-1042.
    number: Mapped[str] = mapped_column(String(32), unique=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    status: Mapped[OrderStatus] = mapped_column(str_enum(OrderStatus, "order_status"))
    currency: Mapped[str] = mapped_column(String(3))
    placed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Set once the order ships with ShipTrack.
    tracking_number: Mapped[str | None] = mapped_column(String(64))
    # Free text from the customer at checkout. Untrusted: may contain prompt injection.
    notes: Mapped[str | None] = mapped_column(String(2000))

    customer: Mapped[Customer] = relationship(back_populates="orders", lazy="raise")
    items: Mapped[list[OrderItem]] = relationship(
        back_populates="order", lazy="raise", cascade="all, delete-orphan"
    )

    @property
    def total_cents(self) -> int:
        """Order total. Requires items to be loaded, e.g. with selectinload(Order.items)."""
        return sum(item.line_total_cents for item in self.items)


class OrderItem(Base):
    __tablename__ = "order_items"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("unit_price_cents >= 0", name="unit_price_not_negative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    quantity: Mapped[int]
    # Price at the time of purchase; product prices can change later.
    unit_price_cents: Mapped[int]

    order: Mapped[Order] = relationship(back_populates="items", lazy="raise")
    product: Mapped[Product] = relationship(lazy="raise")

    @property
    def line_total_cents(self) -> int:
        return self.quantity * self.unit_price_cents
