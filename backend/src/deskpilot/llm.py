"""Chat model factory.

The only module that knows about concrete providers. Everything else asks for a
model by role and receives a LangChain BaseChatModel, so switching a role from
Ollama to Anthropic is purely a configuration change.
"""

from __future__ import annotations

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from langchain_ollama import ChatOllama

from deskpilot.config import ModelRole, ModelSettings, Provider, Settings, get_settings


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
