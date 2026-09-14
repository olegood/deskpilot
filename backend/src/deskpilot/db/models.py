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

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    MetaData,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Output size of the embedding model. Must match DESKPILOT_EMBEDDINGS__DIMENSIONS;
# a vector column's width is fixed in the schema, so changing model needs a migration.
EMBEDDING_DIMENSIONS = 768

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


class TicketCategory(StrEnum):
    """What a ticket is about. Decided by the classifier, and only ever advisory."""

    SHIPPING = "shipping"
    RETURN_OR_REFUND = "return_or_refund"
    WARRANTY = "warranty"
    ORDER_STATUS = "order_status"
    PRODUCT = "product"
    OTHER = "other"


class TicketStatus(StrEnum):
    # The agent (or a human) still owes the customer a reply.
    OPEN = "open"
    # The agent has replied; the ball is with the customer.
    AWAITING_CUSTOMER = "awaiting_customer"
    # Handed to a human, e.g. because the agent ran out of steps.
    ESCALATED = "escalated"
    RESOLVED = "resolved"


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
    tickets: Mapped[list[Ticket]] = relationship(back_populates="customer", lazy="raise")


class Ticket(Base):
    """One support conversation.

    A ticket is also one LangGraph thread: `thread_id` is the key its checkpoints are
    stored under, so the conversation itself lives in LangGraph's tables rather than
    being duplicated here. This row holds only what needs to be queried and listed.
    """

    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Public reference the customer sees and types, e.g. TCK-0007.
    reference: Mapped[str] = mapped_column(String(32), unique=True)
    # Key for this ticket's LangGraph checkpoints. Separate from the reference so a
    # change to how references are formatted can never orphan a conversation.
    thread_id: Mapped[str] = mapped_column(String(64), unique=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    # Written by the customer. Untrusted text: it is not shown to the model yet.
    subject: Mapped[str] = mapped_column(String(200))
    status: Mapped[TicketStatus] = mapped_column(
        str_enum(TicketStatus, "ticket_status"), index=True
    )
    # Filled in by the classifier on the first turn. Nullable because a ticket exists
    # for a moment before it has been read, and because classification can fail.
    category: Mapped[TicketCategory | None] = mapped_column(
        str_enum(TicketCategory, "ticket_category"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    customer: Mapped[Customer] = relationship(back_populates="tickets", lazy="raise")


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
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    quantity: Mapped[int]
    # Price at the time of purchase; product prices can change later.
    unit_price_cents: Mapped[int]

    order: Mapped[Order] = relationship(back_populates="items", lazy="raise")
    product: Mapped[Product] = relationship(lazy="raise")

    @property
    def line_total_cents(self) -> int:
        return self.quantity * self.unit_price_cents


class PolicyChunk(Base):
    """One searchable passage of the Acme Gear policy documents.

    The markdown files under backend/policies are the source of truth. This table is
    a derived index that `deskpilot policy index` rebuilds, so it is safe to delete
    and regenerate at any time.
    """

    __tablename__ = "policy_chunks"
    __table_args__ = (
        # Cosine distance, matching the <=> operator the search query uses. Without a
        # matching operator class the index is silently ignored.
        Index(
            "ix_policy_chunks_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # Source file name, e.g. returns-and-refunds.md.
    document: Mapped[str] = mapped_column(String(200), index=True)
    # Heading path within the document, e.g. "Returns and refunds > Return window".
    heading: Mapped[str] = mapped_column(String(400))
    # Position within the document, so passages can be shown in reading order.
    ordinal: Mapped[int]
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIMENSIONS))
    # Digest of the whole source file, so a changed file is detected without
    # re-embedding anything to find out.
    source_sha256: Mapped[str] = mapped_column(String(64), index=True)
    # Identifies the model, prefix, and dimensions that produced this vector.
    # Vectors with different fingerprints are not comparable, so a change here
    # marks the document stale rather than corrupting search results quietly.
    embedding_fingerprint: Mapped[str] = mapped_column(String(200))
    indexed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
