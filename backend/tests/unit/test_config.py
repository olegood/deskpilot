"""Unit tests for settings loading and validation."""

import os

import pytest
from pydantic import ValidationError

from deskpilot.config import ModelRole, Provider, Settings


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Isolate every test from the developer's shell environment."""
    for key in list(os.environ):
        if key.startswith("DESKPILOT_"):
            monkeypatch.delenv(key)


def load() -> Settings:
    """Build settings from environment variables only, ignoring backend/.env."""
    return Settings(_env_file=None)


def test_defaults_use_ollama_for_every_role():
    settings = load()
    for role in ModelRole:
        assert settings.model_for(role).provider is Provider.OLLAMA
    assert settings.agent.num_ctx == 32_768
    assert str(settings.ollama_base_url).startswith("http://127.0.0.1:11434")


def test_model_for_returns_matching_role():
    settings = load()
    assert settings.model_for(ModelRole.AGENT) is settings.agent
    assert settings.model_for(ModelRole.GUARD) is settings.guard
    assert settings.model_for(ModelRole.JUDGE) is settings.judge


def test_env_overrides_a_single_nested_field(monkeypatch):
    monkeypatch.setenv("DESKPILOT_AGENT__MODEL", "qwen3:8b")
    settings = load()
    assert settings.agent.model == "qwen3:8b"
    assert settings.agent.num_ctx == 32_768


def test_partial_override_keeps_role_specific_defaults(monkeypatch):
    monkeypatch.setenv("DESKPILOT_GUARD__MODEL", "qwen3:8b")
    settings = load()
    assert settings.guard.model == "qwen3:8b"
    assert settings.guard.max_output_tokens == 256
    assert settings.guard.reasoning is False


def test_anthropic_role_requires_api_key(monkeypatch):
    monkeypatch.setenv("DESKPILOT_AGENT__PROVIDER", "anthropic")
    monkeypatch.setenv("DESKPILOT_AGENT__MODEL", "claude-sonnet-5")
    with pytest.raises(ValidationError, match="DESKPILOT_ANTHROPIC_API_KEY"):
        load()


def test_anthropic_role_with_key_is_valid(monkeypatch):
    monkeypatch.setenv("DESKPILOT_AGENT__PROVIDER", "anthropic")
    monkeypatch.setenv("DESKPILOT_AGENT__MODEL", "claude-sonnet-5")
    monkeypatch.setenv("DESKPILOT_ANTHROPIC_API_KEY", "sk-ant-test")
    settings = load()
    assert settings.agent.provider == Provider.ANTHROPIC
    assert settings.anthropic_api_key is not None
    assert settings.anthropic_api_key.get_secret_value() == "sk-ant-test"


def test_switching_provider_without_model_fails(monkeypatch):
    monkeypatch.setenv("DESKPILOT_AGENT__PROVIDER", "anthropic")
    monkeypatch.setenv("DESKPILOT_ANTHROPIC_API_KEY", "sk-ant-test")
    with pytest.raises(ValidationError, match="not a Claude model"):
        load()


def test_api_key_never_appears_in_repr(monkeypatch):
    monkeypatch.setenv("DESKPILOT_ANTHROPIC_API_KEY", "sk-ant-test")
    assert "sk-ant-test" not in repr(load())


def test_rejects_too_small_context(monkeypatch):
    monkeypatch.setenv("DESKPILOT_AGENT__NUM_CTX", "512")
    with pytest.raises(ValidationError):
        load()


def test_rejects_invalid_ollama_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DESKPILOT_OLLAMA_BASE_URL", "not a url")
    with pytest.raises(ValidationError):
        load()
