"""Searching the policy index."""

from __future__ import annotations

from dataclasses import dataclass

from langchain_core.embeddings import Embeddings
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from deskpilot.config import PolicySearchSettings
from deskpilot.db.models import PolicyChunk


@dataclass(frozen=True)
class PolicyPassage:
    document: str
    heading: str
    content: str
    # Cosine distance: 0 is identical, 2 is opposite. Lower is a better match.
    distance: float


async def search_policy_index(
    session: AsyncSession,
    embeddings: Embeddings,
    question: str,
    settings: PolicySearchSettings,
) -> list[PolicyPassage]:
    """Find the passages closest to a question, nearest first.

    Passages further away than max_distance are dropped rather than returned as weak
    matches. A vector search always returns its k nearest neighbours, even when
    nothing is relevant, and handing the model an off-topic passage invites it to
    answer from it.
    """
    question = question.strip()
    if not question:
        return []
    vector = await embeddings.aembed_query(question)

    distance = PolicyChunk.embedding.cosine_distance(vector).label("distance")
    rows = await session.execute(
        select(PolicyChunk, distance).order_by(distance).limit(settings.top_k)
    )
    return [
        PolicyPassage(
            document=chunk.document,
            heading=chunk.heading,
            content=chunk.content,
            distance=float(value),
        )
        for chunk, value in rows
        if value <= settings.max_distance
    ]
