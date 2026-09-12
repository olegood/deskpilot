"""Integration tests for migrations, seeding, and model constraints.

Run with: uv run pytest -m integration (needs `docker compose up -d`).
"""

import pytest
from alembic import command
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, InvalidRequestError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.orm import selectinload

from deskpilot.config import DatabaseSettings
from deskpilot.db.models import Order, OrderStatus
from deskpilot.db.seed import CUSTOMERS, ORDERS, PRODUCTS, SeedError, seed
from deskpilot.db.session import create_session_factory

from .conftest import alembic_config

pytestmark = [pytest.mark.integration]


def test_migrations_match_models(test_database: DatabaseSettings):
    """Fails if a model changed without a migration (alembic check)."""
    command.check(alembic_config(test_database))


async def test_seed_loads_expected_data(engine: AsyncEngine):
    session_factory = create_session_factory(engine)
    async with session_factory() as session:
        result = await seed(session, reset=True)
    assert (result.customers, result.products, result.orders) == (
        len(CUSTOMERS),
        len(PRODUCTS),
        len(ORDERS),
    )

    async with session_factory() as session:
        order = await session.scalar(
            select(Order)
            .where(Order.number == "ORD-1042")
            .options(selectinload(Order.items), selectinload(Order.customer))
        )
    assert order is not None
    assert order.customer.email == "noah.kim@example.com"
    assert order.status is OrderStatus.SHIPPED
    assert order.currency == "USD"
    assert order.total_cents == 17_700


async def test_seed_refuses_to_overwrite_without_reset(engine: AsyncEngine) -> None:
    session_factory = create_session_factory(engine)
    async with session_factory() as session:
        await seed(session, reset=True)
    async with session_factory() as session:
        with pytest.raises(SeedError, match="already has shop data"):
            await seed(session)


async def test_implicit_lazy_loading_is_blocked(engine: AsyncEngine) -> None:
    session_factory = create_session_factory(engine)
    async with session_factory() as session:
        await seed(session, reset=True)
    async with session_factory() as session:
        order = await session.scalar(select(Order).where(Order.number == "ORD-1042"))
        assert order is not None
        with pytest.raises(InvalidRequestError):
            _ = order.items


async def test_database_rejects_unknown_order_status(engine: AsyncEngine) -> None:
    session_factory = create_session_factory(engine)
    async with session_factory() as session:
        await seed(session, reset=True)
    async with session_factory() as session:
        await _expect_integrity_error(
            session, "UPDATE orders SET status = 'lost' WHERE number = 'ORD-1042'"
        )


async def test_database_rejects_non_positive_quantity(engine: AsyncEngine) -> None:
    session_factory = create_session_factory(engine)
    async with session_factory() as session:
        await seed(session, reset=True)
    async with session_factory() as session:
        await _expect_integrity_error(session, "UPDATE order_items SET quantity = 0")


async def _expect_integrity_error(session: AsyncSession, sql: str) -> None:
    with pytest.raises(IntegrityError):
        async with session.begin():
            await session.execute(text(sql))
