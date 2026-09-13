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

from deskpilot.tools.orders import get_order

ALL_TOOLS: list[BaseTool] = [get_order]

__all__ = ["ALL_TOOLS", "get_order"]
