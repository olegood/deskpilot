"""Tools the agent can call.

Every tool follows the same rules:
- The model supplies only business arguments (an order number, for example).
  Identity and dependencies come from AgentContext via ToolRuntime, so the model
  cannot forge them.
- Tools return text written for the model, not raw database rows.
- A tool never reveals data outside the acting customer's scope, and says the same
  thing for "does not exist" and "not yours", so the agent cannot be used to probe
  which order numbers are real.
"""

from langchain_core.tools import BaseTool

from deskpilot.tools.customers import get_customer
from deskpilot.tools.orders import get_order, list_orders
from deskpilot.tools.policy import search_policy

# Order matters a little: the model reads these as a list, and the ones it should
# reach for first are listed first.
ALL_TOOLS: list[BaseTool] = [get_order, list_orders, get_customer, search_policy]

__all__ = ["ALL_TOOLS", "get_customer", "get_order", "list_orders", "search_policy"]
