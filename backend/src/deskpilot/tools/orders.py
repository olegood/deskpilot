"""Order lookup tool."""

from __future__ import annotations

from langchain_core.tools import tool
from langgraph.prebuilt import ToolRuntime
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from deskpilot.db.models import Order, OrderItem
from deskpilot.graph.context import AgentContext

# Same answer whether the order does not exist or belongs to someone else, so the
# tool cannot be used to discover which order numbers are real.
NOT_FOUND = "No order with that number was found for this customer."


def format_money(cents: int, currency: str) -> str:
    return f"{cents / 100:.2f} {currency}"


def describe(order: Order) -> str:
    """Render an order as text for the model. Requires items and product to be loaded."""
    lines = [
        f"Order {order.number}",
        f"Status: {order.status.value}",
        f"Placed: {order.placed_at.date().isoformat()}",
        f"Tracking number: {order.tracking_number or 'not shipped yet'}",
        f"Total: {format_money(order.total_cents, order.currency)}",
        "Items:",
    ]
    lines.extend(
        f"- {item.quantity} x {item.product.name} "
        f"({format_money(item.line_total_cents, order.currency)})"
        for item in order.items
    )
    # order.notes is deliberately left out: it is untrusted text typed by the
    # customer at checkout, and it reaches the model only once spotlighting and the
    # injection guard exist (security milestone).
    return "\n".join(lines)


@tool
async def get_order(order_number: str, runtime: ToolRuntime[AgentContext]) -> str:
    """Look up one of this customer's orders by its number, for example ORD-1042.

    Returns the status, date, tracking number, total, and items.
    """
    context = runtime.context
    async with context.session_factory() as session:
        order = await session.scalar(
            select(Order)
            .where(
                Order.number == order_number.strip().upper(),
                # Ownership check in the query itself: another customer's order can
                # never be loaded, whatever the model asks for.
                Order.customer.has(email=context.customer_email),
            )
            .options(selectinload(Order.items).selectinload(OrderItem.product))
        )
    return describe(order) if order is not None else NOT_FOUND
