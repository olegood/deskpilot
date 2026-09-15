"""Order lookup tools."""

from __future__ import annotations

from langchain_core.tools import tool
from langgraph.prebuilt import ToolRuntime
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from deskpilot.authz import resources
from deskpilot.authz.actions import Action
from deskpilot.authz.audit import guard
from deskpilot.authz.engine import Forbidden
from deskpilot.db.models import Order, OrderItem, OrderStatus
from deskpilot.graph.context import AgentContext

# Same answer whether the order does not exist or the policy refused it, so the
# tool cannot be used to discover which order numbers are real. The audit log
# records which of the two actually happened.
NOT_FOUND = "No order with that number was found for this customer."
NO_ORDERS = "This customer has not placed any orders."
NONE_MATCHING = "This customer has no orders with that status."


def format_money(cents: int, currency: str) -> str:
    return f"{cents / 100:.2f} {currency}"


def summarise(order: Order) -> str:
    """One line per order, for a list. Requires items to be loaded."""
    return (
        f"{order.number}  {order.placed_at.date().isoformat()}  "
        f"{order.status.value:<10}  {format_money(order.total_cents, order.currency)}"
    )


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
            .where(Order.number == order_number.strip().upper())
            .options(
                selectinload(Order.items).selectinload(OrderItem.product),
                # Needed to name the order's region to the policy. lazy="raise"
                # means forgetting this fails loudly rather than intermittently.
                selectinload(Order.customer),
            )
        )
    if order is None:
        return NOT_FOUND

    # The row is loaded first and judged second, so the policy decides ownership
    # and the attempt is recorded. The row never leaves this function when the
    # answer is no.
    try:
        await guard(
            context.session_factory,
            context.principal,
            Action.ORDER_VIEW,
            resources.Order(owner_customer_id=order.customer_id, region=order.customer.region),
        )
    except Forbidden:
        return NOT_FOUND
    return describe(order)


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
    principal = context.principal
    # A list cannot be judged row by row, so the decision is about the scope: may
    # this principal read the orders of the customer they are? Staff, who own no
    # customer record, are refused here rather than shown somebody else's list.
    try:
        await guard(
            context.session_factory,
            principal,
            Action.ORDER_VIEW,
            resources.Order(
                owner_customer_id=principal.customer_id or -1,
                region=principal.home_region,
            ),
        )
    except Forbidden:
        return NO_ORDERS

    limit = context.tools.max_orders_listed
    async with context.session_factory() as session:
        owned = Order.customer_id == principal.customer_id
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
