"""Chat model factory.

The only module that knows about concrete providers. Everything else asks for a
model by role and receives a LangChain BaseChatModel, so switching a role from
Ollama to Anthropic is purely a configuration change.
"""

from __future__ import annotations

from dataclasses import dataclass

from langchain_anthropic import ChatAnthropic
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel
from langchain_ollama import ChatOllama, OllamaEmbeddings

from deskpilot.config import (
    EmbeddingSettings,
    ModelRole,
    ModelSettings,
    Provider,
    Settings,
    get_settings,
)


def build_chat_model(role: ModelRole, settings: Settings | None = None) -> BaseChatModel:
    """Build a new chat model for a role, as configured in settings."""
    settings = settings or get_settings()
    model_settings = settings.model_for(role)
    match model_settings.provider:
        case Provider.OLLAMA:
            return _build_ollama(model_settings, settings)
        case Provider.ANTHROPIC:
            return _build_anthropic(model_settings, settings)


def _build_ollama(model: ModelSettings, settings: Settings) -> ChatOllama:
    return ChatOllama(
        model=model.model,
        base_url=str(settings.ollama_base_url).rstrip("/"),
        temperature=model.temperature,
        num_ctx=model.num_ctx,
        num_predict=model.max_output_tokens,
        reasoning=model.reasoning,
        # Passed to the underlying httpx clients (sync and async).
        client_kwargs={"timeout": model.timeout_s},
    )


def _build_anthropic(model: ModelSettings, settings: Settings) -> ChatAnthropic:
    if settings.anthropic_api_key is None:  # Settings validation normally prevents this.
        raise ValueError("DESKPILOT_ANTHROPIC_API_KEY is not set")
    return ChatAnthropic(
        model=model.model,
        api_key=settings.anthropic_api_key,
        temperature=model.temperature,
        max_tokens=model.max_output_tokens,
        timeout=model.timeout_s,
    )


@dataclass
class PrefixedEmbeddings(Embeddings):
    """Applies a model's task prefixes to text before embedding it.

    Retrieval models are often trained to be told what a piece of text is for, and
    nomic-embed-text is one of them: documents get "search_document: " and questions
    get "search_query: ". Skipping this is not an error, it just makes every result
    slightly worse, which is exactly the kind of bug that never gets noticed.
    """

    inner: Embeddings
    document_prefix: str
    query_prefix: str

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self.inner.embed_documents([self.document_prefix + text for text in texts])

    def embed_query(self, text: str) -> list[float]:
        return self.inner.embed_query(self.query_prefix + text)

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return await self.inner.aembed_documents([self.document_prefix + text for text in texts])

    async def aembed_query(self, text: str) -> list[float]:
        return await self.inner.aembed_query(self.query_prefix + text)


def build_embeddings(settings: Settings | None = None) -> Embeddings:
    """Build the embedding model.

    Always Ollama: Anthropic has no embeddings API, so this does not follow the
    per-role provider switch.
    """
    settings = settings or get_settings()
    return _with_prefixes(settings.embeddings, str(settings.ollama_base_url).rstrip("/"))


def _with_prefixes(embeddings: EmbeddingSettings, base_url: str) -> Embeddings:
    inner = OllamaEmbeddings(
        model=embeddings.model,
        base_url=base_url,
        num_ctx=embeddings.num_ctx,
        client_kwargs={"timeout": embeddings.timeout_s},
    )
    if not embeddings.document_prefix and not embeddings.query_prefix:
        return inner
    return PrefixedEmbeddings(inner, embeddings.document_prefix, embeddings.query_prefix)
