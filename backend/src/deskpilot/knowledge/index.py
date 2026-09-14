"""Building and inspecting the policy index.

The markdown files are the source of truth; this table is derived from them and can
be rebuilt at any time. Indexing is per document, so editing one file re-embeds that
file only.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from langchain_core.embeddings import Embeddings
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from deskpilot.config import Settings, get_settings
from deskpilot.db.models import EMBEDDING_DIMENSIONS, PolicyChunk
from deskpilot.knowledge.chunking import chunk_document, read_documents, sha256_of


class PolicyIndexError(Exception):
    """Raised when the index cannot be built."""


class DocumentState(StrEnum):
    CURRENT = "current"
    # On disk but never indexed.
    MISSING = "missing"
    # Indexed, but the file or the embedding model has changed since.
    STALE = "stale"
    # Indexed but the file is gone from disk.
    ORPHANED = "orphaned"


@dataclass(frozen=True)
class DocumentStatus:
    document: str
    state: DocumentState
    chunks: int


@dataclass(frozen=True)
class IndexResult:
    documents_indexed: int
    documents_skipped: int
    documents_removed: int
    chunks_written: int


@dataclass(frozen=True)
class IndexedDocument:
    """What the index holds for one document."""

    chunks: int
    # Every distinct (source digest, embedding fingerprint) pair among its passages.
    # Normally one. More than one means a half-finished rebuild, which counts as
    # stale: a document is only current when all of its passages agree.
    versions: set[tuple[str, str]]

    def matches(self, digest: str, fingerprint: str) -> bool:
        return self.versions == {(digest, fingerprint)}


async def indexed_documents(session: AsyncSession) -> dict[str, IndexedDocument]:
    """What the index currently holds, keyed by document."""
    rows = await session.execute(
        select(PolicyChunk.document, PolicyChunk.source_sha256, PolicyChunk.embedding_fingerprint)
    )
    found: dict[str, IndexedDocument] = {}
    for document, digest, fingerprint in rows:
        entry = found.get(document, IndexedDocument(chunks=0, versions=set()))
        found[document] = IndexedDocument(
            chunks=entry.chunks + 1,
            versions=entry.versions | {(digest, fingerprint)},
        )
    return found


async def index_status(
    session: AsyncSession, settings: Settings | None = None
) -> list[DocumentStatus]:
    """Compare the files on disk with what has been indexed."""
    settings = settings or get_settings()
    on_disk = read_documents(settings.policy_search.path)
    indexed = await indexed_documents(session)
    fingerprint = settings.embeddings.fingerprint

    statuses: list[DocumentStatus] = []
    for document, text in on_disk.items():
        entry = indexed.get(document)
        if entry is None:
            statuses.append(DocumentStatus(document, DocumentState.MISSING, 0))
            continue
        # A different digest means the file changed; a different fingerprint means
        # every vector in it is incomparable with the ones we would produce now.
        state = (
            DocumentState.CURRENT
            if entry.matches(sha256_of(text), fingerprint)
            else DocumentState.STALE
        )
        statuses.append(DocumentStatus(document, state, entry.chunks))

    statuses.extend(
        DocumentStatus(document, DocumentState.ORPHANED, indexed[document].chunks)
        for document in sorted(set(indexed) - set(on_disk))
    )
    return sorted(statuses, key=lambda status: status.document)


async def build_index(
    session: AsyncSession,
    embeddings: Embeddings,
    settings: Settings | None = None,
    force: bool = False,
) -> IndexResult:
    """Bring the index in line with the files on disk. Does not commit.

    Unchanged documents are skipped, so editing one policy file re-embeds that file
    only. `force` re-embeds everything, which is what you want after changing the
    chunking rules, since the digest would not have changed.
    """
    settings = settings or get_settings()
    on_disk = read_documents(settings.policy_search.path)
    if not on_disk:
        raise PolicyIndexError(f"no markdown files in {settings.policy_search.path}")
    statuses = {status.document: status.state for status in await index_status(session, settings)}

    indexed = skipped = removed = written = 0
    for document, text in on_disk.items():
        if not force and statuses.get(document) is DocumentState.CURRENT:
            skipped += 1
            continue
        chunks = chunk_document(document, text, settings.policy_search.max_chunk_chars)
        if not chunks:
            raise PolicyIndexError(f"{document} produced no passages; does it have any headings?")
        vectors = await embeddings.aembed_documents([chunk.content for chunk in chunks])
        for vector in vectors:
            if len(vector) != EMBEDDING_DIMENSIONS:
                raise PolicyIndexError(
                    f"{settings.embeddings.model} returned {len(vector)}-dimension vectors "
                    f"but the database column holds {EMBEDDING_DIMENSIONS}; changing model "
                    "needs a migration"
                )
        # Replace the document's passages wholesale: simpler than diffing chunks, and
        # the cost is one extra delete on a table with tens of rows.
        await session.execute(delete(PolicyChunk).where(PolicyChunk.document == document))
        digest = sha256_of(text)
        session.add_all(
            PolicyChunk(
                document=chunk.document,
                heading=chunk.heading,
                ordinal=chunk.ordinal,
                content=chunk.content,
                embedding=vector,
                source_sha256=digest,
                embedding_fingerprint=settings.embeddings.fingerprint,
            )
            for chunk, vector in zip(chunks, vectors, strict=True)
        )
        indexed += 1
        written += len(chunks)

    orphans = [doc for doc, state in statuses.items() if state is DocumentState.ORPHANED]
    if orphans:
        await session.execute(delete(PolicyChunk).where(PolicyChunk.document.in_(orphans)))
        removed = len(orphans)

    return IndexResult(indexed, skipped, removed, written)
