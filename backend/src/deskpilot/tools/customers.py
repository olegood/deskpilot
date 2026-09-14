"""Customer profile tool."""

from __future__ import annotations

from langchain_core.tools import tool
from langgraph.prebuilt import ToolRuntime
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from deskpilot.db.models import Customer, Order
from deskpilot.graph.context import AgentContext

NOT_FOUND = "This customer's account could not be loaded."


def describe(customer: Customer, order_count: int) -> str:
    """Render a customer's own account details for the model."""
    return "\n".join(
        [
            f"Name: {customer.full_name}",
            f"Email: {customer.email}",
            f"Region: {customer.region.value}",
            # The tier decides several policy outcomes, such as the return window,
            # so the agent needs it before it can apply the policy correctly.
            f"Membership tier: {customer.tier.value}",
            f"Customer since: {customer.created_at.date().isoformat()}",
            f"Orders placed: {order_count}",
        ]
    )


@tool
async def get_customer(runtime: ToolRuntime[AgentContext]) -> str:
    """Look up the account details of the customer you are talking to.

    Takes no arguments: it always describes the current customer. Use it when their
    membership tier, region, or how long they have been a customer affects the
    answer.
    """
    context = runtime.context
    async with context.session_factory() as session:
        customer = await session.scalar(
            select(Customer)
            .where(Customer.email == context.customer_email)
            .options(selectinload(Customer.orders))
        )
        if customer is None:
            return NOT_FOUND
        order_count = await session.scalar(
            select(func.count()).select_from(Order).where(Order.customer_id == customer.id)
        )
    return describe(customer, order_count or 0)
