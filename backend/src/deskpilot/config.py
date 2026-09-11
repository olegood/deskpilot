"""Deskpilot settings.

Every value comes from an environment variable with the DESKPILOT_ prefix, or from
backend/.env. Nested fields use a double underscore, for example
DESKPILOT_AGENT__MODEL=qwen3:8b overrides only the agent's model name.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/src/deskpilot/config.py -> parents[2] is backend/
BACKEND_DIR = Path(__file__).resolve().parents[2]


class Provider(StrEnum):
    OLLAMA = "ollama"
    ANTHROPIC = "anthropic"


class ModelRole(StrEnum):
    """Each role has its own model settings and can switch provider independently."""

    AGENT = "agent"
    GUARD = "guard"
    JUDGE = "judge"


class ModelSettings(BaseModel):
    """How to build one chat model."""

    model_config = ConfigDict(frozen=True)

    provider: Provider = Provider.OLLAMA
    model: str = Field(min_length=1)
    temperature: float = Field(default=0.0, ge=0.0, le=1.0)
    max_output_tokens: int = Field(default=2048, gt=0)
    timeout_s: float = Field(default=120.0, gt=0)
    # Ollama only. Must be set explicitly: when a prompt exceeds the window, Ollama
    # silently drops the oldest tokens, which removes the system prompt first.
    num_ctx: int = Field(default=32_768, ge=2_048)
    # Thinking mode, always explicit. Leaving it unset lets thinking models put raw
    # <think> blocks into the response content, which could reach customers.
    reasoning: bool = False

    @model_validator(mode="after")
    def _model_matches_provider(self) -> Self:
        if self.provider is Provider.ANTHROPIC and not self.model.startswith("claude-"):
            raise ValueError(
                f"provider is anthropic but model {self.model!r} is not a Claude model; "
                "set the MODEL variable for this role too"
            )
        return self

    @model_validator(mode="after")
    def _reasoning_supported(self) -> Self:
        if self.provider is Provider.ANTHROPIC and self.reasoning:
            raise ValueError(
                "reasoning is not supported for anthropic yet; it arrives with the "
                "Anthropic switch milestone"
            )
        return self


class EmbeddingSettings(BaseModel):
    """Embeddings stay on Ollama after the LLM switch: Anthropic has no embeddings API."""

    model_config = ConfigDict(frozen=True)

    provider: Literal["ollama"] = "ollama"
    model: str = Field(default="nomic-embed-text", min_length=1)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DESKPILOT_",
        env_nested_delimiter="__",
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # Without this, DESKPILOT_GUARD__MODEL=x would rebuild the guard settings from
        # ModelSettings defaults and silently drop the guard-specific values below.
        nested_model_default_partial_update=True,
    )

    ollama_base_url: AnyHttpUrl = AnyHttpUrl("http://127.0.0.1:11434")
    anthropic_api_key: SecretStr | None = None

    agent: ModelSettings = ModelSettings(model="qwen3.6:35b")
    guard: ModelSettings = ModelSettings(
        model="qwen3.6:35b",
        max_output_tokens=256,
        timeout_s=30.0,
    )
    judge: ModelSettings = ModelSettings(
        model="gpt-oss:20b",
        max_output_tokens=1024,
        timeout_s=300.0,
    )
    embeddings: EmbeddingSettings = EmbeddingSettings()

    def model_for(self, role: ModelRole) -> ModelSettings:
        match role:
            case ModelRole.AGENT:
                return self.agent
            case ModelRole.GUARD:
                return self.guard
            case ModelRole.JUDGE:
                return self.judge

    @model_validator(mode="after")
    def _anthropic_needs_api_key(self) -> Self:
        roles = [r.value for r in ModelRole if self.model_for(r).provider is Provider.ANTHROPIC]
        if roles and self.anthropic_api_key is None:
            raise ValueError(
                "DESKPILOT_ANTHROPIC_API_KEY is required because these roles use "
                f"anthropic: {', '.join(roles)}"
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load settings once per process."""
    return Settings()
