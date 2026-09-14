"""Integration tests for the policy index: building it, keeping it current, searching it.

These use the real embedding model, so they need Ollama as well as PostgreSQL.
Run with: uv run pytest -m integration
"""

import pytest
from langchain_core.embeddings import Embeddings
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deskpilot.config import Settings
from deskpilot.db.models import PolicyChunk
from deskpilot.knowledge.index import (
    DocumentState,
    PolicyIndexError,
    build_index,
    index_status,
)
from deskpilot.knowledge.search import search_policy_index
from deskpilot.llm import build_embeddings

pytestmark = pytest.mark.integration


@pytest.fixture
def embeddings(agent_settings: Settings) -> Embeddings:
    return build_embeddings(agent_settings)


@pytest.fixture
async def indexed(
    seeded_sessions: async_sessionmaker[AsyncSession],
    embeddings: Embeddings,
    agent_settings: Settings,
) -> async_sessionmaker[AsyncSession]:
    """A freshly built index over the real policy documents.

    Policy chunks are cleared first. The shop seed does not touch this table, so a
    row left behind by another test would otherwise leak into this one.
    """
    async with seeded_sessions() as session:
        await session.execute(delete(PolicyChunk))
        await build_index(session, embeddings, agent_settings, force=True)
        await session.commit()
    return seeded_sessions


async def test_indexing_writes_passages_for_every_document(
    indexed: async_sessionmaker[AsyncSession], agent_settings: Settings
) -> None:
    async with indexed() as session:
        statuses = await index_status(session, agent_settings)
        total = await session.scalar(select(func.count()).select_from(PolicyChunk))

    assert statuses, "no policy documents were found on disk"
    assert all(status.state is DocumentState.CURRENT for status in statuses)
    assert total == sum(status.chunks for status in statuses)


async def test_an_unchanged_document_is_not_re_embedded(
    indexed: async_sessionmaker[AsyncSession], embeddings: Embeddings, agent_settings: Settings
) -> None:
    async with indexed() as session:
        result = await build_index(session, embeddings, agent_settings)
        await session.commit()

    assert result.documents_indexed == 0
    assert result.documents_skipped > 0
    assert result.chunks_written == 0


async def test_force_re_embeds_everything(
    indexed: async_sessionmaker[AsyncSession], embeddings: Embeddings, agent_settings: Settings
) -> None:
    async with indexed() as session:
        result = await build_index(session, embeddings, agent_settings, force=True)
        await session.commit()

    assert result.documents_skipped == 0
    assert result.chunks_written > 0


async def test_a_changed_document_is_detected_as_stale(
    indexed: async_sessionmaker[AsyncSession], agent_settings: Settings
) -> None:
    """Staleness is a digest comparison, so it costs nothing to check."""
    async with indexed() as session:
        # Simulate an edit by corrupting the stored digest of one document. Ordering
        # matters: an unordered limit(1) picks an arbitrary row, which made this test
        # pass or fail depending on what ran before it.
        chunk = await session.scalar(select(PolicyChunk).order_by(PolicyChunk.id).limit(1))
        assert chunk is not None
        edited = chunk.document
        chunk.source_sha256 = "0" * 64
        await session.commit()

    async with indexed() as session:
        states = {s.document: s.state for s in await index_status(session, agent_settings)}

    assert states[edited] is DocumentState.STALE
    assert any(state is DocumentState.CURRENT for state in states.values())


@pytest.mark.parametrize(
    "change",
    [{"model": "other-model"}, {"document_prefix": "passage: "}, {"dimensions": 512}],
    ids=["model", "prefix", "dimensions"],
)
async def test_changing_the_embedding_fingerprint_invalidates_the_index(
    indexed: async_sessionmaker[AsyncSession],
    agent_settings: Settings,
    change: dict[str, object],
) -> None:
    """Any of these changes the vectors for the same text, so the index must be rebuilt."""
    other = agent_settings.model_copy(
        update={"embeddings": agent_settings.embeddings.model_copy(update=change)}
    )

    async with indexed() as session:
        states = [status.state for status in await index_status(session, other)]

    assert states
    assert all(state is DocumentState.STALE for state in states)


async def test_a_document_removed_from_disk_is_reported_and_cleaned_up(
    indexed: async_sessionmaker[AsyncSession], embeddings: Embeddings, agent_settings: Settings
) -> None:
    async with indexed() as session:
        session.add(
            PolicyChunk(
                document="deleted-policy.md",
                heading="Gone",
                ordinal=0,
                content="This document no longer exists on disk.",
                embedding=[0.0] * agent_settings.embeddings.dimensions,
                source_sha256="0" * 64,
                embedding_fingerprint=agent_settings.embeddings.fingerprint,
            )
        )
        await session.commit()

    async with indexed() as session:
        states = {s.document: s.state for s in await index_status(session, agent_settings)}
        assert states["deleted-policy.md"] is DocumentState.ORPHANED

        result = await build_index(session, embeddings, agent_settings)
        await session.commit()

    assert result.documents_removed == 1
    async with indexed() as session:
        documents = {s.document for s in await index_status(session, agent_settings)}
    assert "deleted-policy.md" not in documents


async def test_an_empty_policy_directory_is_an_error(
    seeded_sessions: async_sessionmaker[AsyncSession],
    embeddings: Embeddings,
    agent_settings: Settings,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    empty = agent_settings.model_copy(
        update={
            "policy_search": agent_settings.policy_search.model_copy(
                update={"directory": tmp_path_factory.mktemp("empty")}
            )
        }
    )
    async with seeded_sessions() as session:
        with pytest.raises((PolicyIndexError, FileNotFoundError)):
            await build_index(session, embeddings, empty)


@pytest.mark.parametrize(
    ("question", "expected_document"),
    [
        ("How long do I have to send something back?", "returns.md"),
        ("My parcel never turned up, what happens now?", "shipping.md"),
        ("When will the money be back on my card?", "refunds.md"),
        ("The zip on my rucksack broke after a year", "warranty.md"),
    ],
)
async def test_retrieval_finds_the_right_document(
    indexed: async_sessionmaker[AsyncSession],
    embeddings: Embeddings,
    agent_settings: Settings,
    question: str,
    expected_document: str,
) -> None:
    """The questions deliberately avoid the wording used in the documents.

    This asserts ranking, not distance. Which passage is nearest is a property of
    the model and the documents; what counts as "close enough" is a threshold we
    chose, and pinning a number here would make the test fail whenever the model
    changed without anything actually being wrong.
    """
    # The threshold is deliberately relaxed: this test is about order, not cutoff.
    unfiltered = agent_settings.policy_search.model_copy(update={"max_distance": 2.0})

    async with indexed() as session:
        passages = await search_policy_index(session, embeddings, question, unfiltered)

    assert passages, f"{question!r} returned nothing at all"
    found = [(passage.document, passage.heading) for passage in passages]
    # Among the top k, not strictly first: which of several relevant passages ranks
    # highest varies between models, and pinning it would make this a model test.
    assert expected_document in {document for document, _ in found}, (
        f"{question!r} returned {found}"
    )


async def test_results_are_ordered_by_distance(
    indexed: async_sessionmaker[AsyncSession], embeddings: Embeddings, agent_settings: Settings
) -> None:
    unfiltered = agent_settings.policy_search.model_copy(update={"max_distance": 2.0})

    async with indexed() as session:
        passages = await search_policy_index(
            session, embeddings, "when do refunds reach my card?", unfiltered
        )

    assert len(passages) > 1
    assert passages == sorted(passages, key=lambda passage: passage.distance)


async def test_an_unrelated_question_returns_nothing(
    indexed: async_sessionmaker[AsyncSession], embeddings: Embeddings, agent_settings: Settings
) -> None:
    """A vector search always has nearest neighbours; the threshold is what rejects them."""
    strict = agent_settings.policy_search.model_copy(update={"max_distance": 0.05})

    async with indexed() as session:
        passages = await search_policy_index(
            session, embeddings, "what is the atomic mass of tungsten?", strict
        )

    assert passages == []


async def test_an_empty_question_does_not_reach_the_database(
    indexed: async_sessionmaker[AsyncSession], embeddings: Embeddings, agent_settings: Settings
) -> None:
    async with indexed() as session:
        assert (
            await search_policy_index(session, embeddings, "   ", agent_settings.policy_search)
            == []
        )
