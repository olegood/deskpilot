"""Runtime context for an agent run.

The context holds who the agent is acting for and what it needs to reach the
database. It is passed to `ainvoke(..., context=...)`, is never part of graph
state, and is never serialized into messages, so the model cannot read or change
it. Tools receive it through `ToolRuntime`.

This is the seed of the identity model: from the ABAC milestone onwards, the
context carries a full principal with attributes, and every tool checks it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from langchain_core.embeddings import Embeddings
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deskpilot.authz.principal import Principal
from deskpilot.config import PolicySearchSettings, ToolSettings


@dataclass(frozen=True)
class AgentContext:
    """Identity and dependencies for one agent run."""

    # Who the agent is acting for, with the attributes a policy weighs. Comes from
    # the authenticated session, never from the ticket text or the model.
    principal: Principal
    session_factory: async_sessionmaker[AsyncSession]
    # Used by tools that search the policy index. A dependency like the session
    # factory, not a model the agent reasons with.
    embeddings: Embeddings | None = None
    # Passed in rather than read from a global, so a tool's behaviour is decided by
    # its caller and a test can vary it without touching the environment.
    policy_search: PolicySearchSettings = field(default_factory=PolicySearchSettings)
    tools: ToolSettings = field(default_factory=ToolSettings)

    @property
    def customer_email(self) -> str:
        """Convenience for logging and messages. Never used to decide anything."""
        return self.principal.email
