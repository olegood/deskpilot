"""Unit tests for the task-prefix wrapper. No model is called."""

from langchain_core.embeddings import Embeddings

from deskpilot.llm import PrefixedEmbeddings


class RecordingEmbeddings(Embeddings):
    """Returns nothing useful, but remembers exactly what it was asked to embed."""

    def __init__(self) -> None:
        self.seen: list[str] = []

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.seen.extend(texts)
        return [[0.0] for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        self.seen.append(text)
        return [0.0]


def wrapped() -> tuple[PrefixedEmbeddings, RecordingEmbeddings]:
    inner = RecordingEmbeddings()
    return PrefixedEmbeddings(inner, "search_document: ", "search_query: "), inner


async def test_documents_and_queries_get_different_prefixes() -> None:
    """The whole point: the model is told what each piece of text is for."""
    embeddings, inner = wrapped()

    await embeddings.aembed_documents(["Returns are accepted within 30 days."])
    await embeddings.aembed_query("how long do I have to return something?")

    assert inner.seen == [
        "search_document: Returns are accepted within 30 days.",
        "search_query: how long do I have to return something?",
    ]


def test_the_sync_path_prefixes_too() -> None:
    embeddings, inner = wrapped()

    embeddings.embed_documents(["a", "b"])

    assert inner.seen == ["search_document: a", "search_document: b"]
