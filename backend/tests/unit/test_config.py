"""Unit tests for settings loading and validation."""

import pytest
from pydantic import ValidationError

from deskpilot.config import ModelRole, Provider, Settings


def load() -> Settings:
    """Build settings from environment variables only, ignoring backend/.env."""
    return Settings(_env_file=None)


def test_defaults_use_ollama_for_every_role() -> None:
    settings = load()
    for role in ModelRole:
        assert settings.model_for(role).provider is Provider.OLLAMA
    assert settings.agent.num_ctx == 32_768
    assert str(settings.ollama_base_url).startswith("http://127.0.0.1:11434")


def test_reasoning_is_off_by_default_for_every_role() -> None:
    settings = load()
    for role in ModelRole:
        assert settings.model_for(role).reasoning is False


def test_model_for_returns_matching_role() -> None:
    settings = load()
    assert settings.model_for(ModelRole.AGENT) is settings.agent
    assert settings.model_for(ModelRole.GUARD) is settings.guard
    assert settings.model_for(ModelRole.JUDGE) is settings.judge


def test_env_overrides_a_single_nested_field(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DESKPILOT_AGENT__MODEL", "qwen3:8b")
    settings = load()
    assert settings.agent.model == "qwen3:8b"
    assert settings.agent.num_ctx == 32_768


def test_partial_override_keeps_role_specific_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DESKPILOT_GUARD__MODEL", "qwen3:8b")
    settings = load()
    assert settings.guard.model == "qwen3:8b"
    assert settings.guard.max_output_tokens == 256
    assert settings.guard.timeout_s == 30.0


def test_reasoning_can_be_enabled_for_ollama(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DESKPILOT_AGENT__REASONING", "true")
    assert load().agent.reasoning is True


def test_anthropic_role_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DESKPILOT_AGENT__PROVIDER", "anthropic")
    monkeypatch.setenv("DESKPILOT_AGENT__MODEL", "claude-sonnet-5")
    with pytest.raises(ValidationError, match="DESKPILOT_ANTHROPIC_API_KEY"):
        load()


def test_anthropic_role_with_key_is_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DESKPILOT_ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("DESKPILOT_AGENT__PROVIDER", "anthropic")
    monkeypatch.setenv("DESKPILOT_AGENT__MODEL", "claude-sonnet-5")
    settings = load()
    assert settings.agent.provider is Provider.ANTHROPIC
    assert settings.anthropic_api_key is not None
    assert settings.anthropic_api_key.get_secret_value() == "sk-ant-test"


def test_switching_provider_without_model_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DESKPILOT_ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("DESKPILOT_AGENT__PROVIDER", "anthropic")
    with pytest.raises(ValidationError, match="not a Claude model"):
        load()


def test_anthropic_rejects_reasoning_for_now(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DESKPILOT_ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("DESKPILOT_AGENT__PROVIDER", "anthropic")
    monkeypatch.setenv("DESKPILOT_AGENT__MODEL", "claude-sonnet-5")
    monkeypatch.setenv("DESKPILOT_AGENT__REASONING", "true")
    with pytest.raises(ValidationError, match="reasoning is not supported"):
        load()


def test_api_key_never_appears_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DESKPILOT_ANTHROPIC_API_KEY", "sk-ant-test")
    assert "sk-ant-test" not in repr(load())


def test_rejects_too_small_context(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DESKPILOT_AGENT__NUM_CTX", "512")
    with pytest.raises(ValidationError):
        load()


def test_database_settings_merge_with_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DESKPILOT_DATABASE__PORT", "6543")
    database = load().database
    assert database.port == 6543
    assert database.name == "deskpilot"


def test_database_url_uses_psycopg_and_settings() -> None:
    url = load().database.url
    assert url.drivername == "postgresql+psycopg"
    assert (url.host, url.port, url.database, url.username) == (
        "127.0.0.1",
        5432,
        "deskpilot",
        "deskpilot",
    )


def test_database_password_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DESKPILOT_DATABASE__PASSWORD")
    with pytest.raises(ValidationError, match="DESKPILOT_DATABASE__PASSWORD"):
        load()


def test_database_password_never_appears_in_repr() -> None:
    settings = load()
    assert "test-password" not in repr(settings)
    assert "test-password" not in repr(settings.database.url)


def test_rejects_invalid_ollama_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DESKPILOT_OLLAMA_BASE_URL", "not a url")
    with pytest.raises(ValidationError):
        load()


def test_embedding_fingerprint_covers_model_prefix_and_dimensions() -> None:
    embeddings = load().embeddings

    assert embeddings.model in embeddings.fingerprint
    assert embeddings.document_prefix in embeddings.fingerprint
    assert str(embeddings.dimensions) in embeddings.fingerprint


def test_embedding_fingerprint_changes_with_the_prefix() -> None:
    embeddings = load().embeddings
    other = embeddings.model_copy(update={"document_prefix": "passage: "})

    assert embeddings.fingerprint != other.fingerprint
