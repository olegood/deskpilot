"""Policy lookup tool."""

from __future__ import annotations

from langchain_core.tools import tool
from langgraph.prebuilt import ToolRuntime

from deskpilot.authz import resources
from deskpilot.authz.actions import Action
from deskpilot.authz.audit import guard
from deskpilot.authz.engine import Forbidden
from deskpilot.graph.context import AgentContext
from deskpilot.knowledge.search import PolicyPassage, search_policy_index

NOTHING_FOUND = (
    "The policy documents do not cover this. Do not guess: tell the customer you "
    "will check with a colleague."
)


def format_passages(passages: list[PolicyPassage]) -> str:
    """Render passages for the model, most relevant first."""
    # The source is named so the model can attribute an answer, and so a wrong answer
    # can be traced back to the passage that caused it.
    return "\n\n---\n\n".join(
        f"[{passage.document} - {passage.heading}]\n{passage.content}" for passage in passages
    )


@tool
async def search_policy(question: str, runtime: ToolRuntime[AgentContext]) -> str:
    """Search Acme Gear's published policies on returns, refunds, shipping, and warranty.

    Ask a full question, for example "how long do I have to return a tent?" rather
    than "returns". Returns the most relevant passages, or says so when the policy
    does not cover the question.
    """
    context = runtime.context
    if context.embeddings is None:
        raise RuntimeError("policy search needs an embedding model in the agent context")
    try:
        await guard(
            context.session_factory,
            context.principal,
            Action.POLICY_SEARCH,
            resources.PolicyDocuments(),
        )
    except Forbidden:
        return NOTHING_FOUND

    async with context.session_factory() as session:
        passages = await search_policy_index(
            session, context.embeddings, question, context.policy_search
        )
    return format_passages(passages) if passages else NOTHING_FOUND
