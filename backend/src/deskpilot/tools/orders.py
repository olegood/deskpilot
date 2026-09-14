"""Order lookup tools."""

from __future__ import annotations

from langchain_core.tools import tool
from langgraph.prebuilt import ToolRuntime
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from deskpilot.db.models import Order, OrderItem, OrderStatus
from deskpilot.graph.context import AgentContext

# Same answer whether the order does not exist or belongs to someone else, so the
# tool cannot be used to discover which order numbers are real.
NOT_FOUND = "No order with that number was found for this customer."
NO_ORDERS = "This customer has not placed any orders."
NONE_MATCHING = "This customer has no orders with that status."


def summarise(order: Order) -> str:
    """One line per order, for a list. Requires items to be loaded."""
    return (
        f"{order.number}  {order.placed_at.date().isoformat()}  "
        f"{order.status.value:<10}  {format_money(order.total_cents, order.currency)}"
    )


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


@tool
async def list_orders(
    runtime: ToolRuntime[AgentContext],
    status: OrderStatus | None = None,
) -> str:
    """List this customer's orders, newest first, one line each.

    Use it when the customer refers to an order without giving its number, such as
    "my last order" or "the one that hasn't arrived". Optionally filter by status.
    Call get_order afterwards for the full detail of a particular order.
    """
    context = runtime.context
    limit = context.tools.max_orders_listed
    async with context.session_factory() as session:
        owned = Order.customer.has(email=context.customer_email)
        conditions = [owned] if status is None else [owned, Order.status == status]

        total = await session.scalar(select(func.count()).select_from(Order).where(*conditions))
        orders = list(
            await session.scalars(
                select(Order)
                .where(*conditions)
                .order_by(Order.placed_at.desc(), Order.id.desc())
                .limit(limit)
                .options(selectinload(Order.items))
            )
        )

    if not orders:
        return NONE_MATCHING if status is not None else NO_ORDERS

    lines = [summarise(order) for order in orders]
    if total and total > len(orders):
        # Said explicitly, because a model shown ten of fourteen orders will
        # otherwise tell the customer they have ten.
        lines.append(
            f"(showing the {len(orders)} most recent of {total}; "
            "ask for a specific order number for the rest)"
        )
    return "\n".join(lines)
