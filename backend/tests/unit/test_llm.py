"""Unit tests for the chat model factory. No model is called."""

from typing import Any

import pytest
from langchain_anthropic import ChatAnthropic
from langchain_ollama import ChatOllama

from deskpilot.config import ModelRole, ModelSettings, Provider, Settings
from deskpilot.llm import build_chat_model


def load(**overrides: Any) -> Settings:
    """Build settings from explicit values only, ignoring backend/.env."""
    return Settings(_env_file=None, **overrides)


def anthropic_agent() -> ModelSettings:
    # reasoning stays False: anthropic thinking is rejected until its own milestone.
    return ModelSettings(provider=Provider.ANTHROPIC, model="claude-sonnet-5", timeout_s=45.0)


def test_ollama_agent_receives_configured_parameters() -> None:
    model = build_chat_model(ModelRole.AGENT, load())
    assert isinstance(model, ChatOllama)
    assert model.model == "qwen3.6:35b"
    assert model.base_url == "http://127.0.0.1:11434"
    assert model.temperature == 0.0
    assert model.num_ctx == 32_768
    assert model.num_predict == 2048
    assert model.reasoning is True  # D-070
    assert model.client_kwargs == {"timeout": 120.0}


def test_each_role_uses_its_own_settings() -> None:
    settings = load()
    guard = build_chat_model(ModelRole.GUARD, settings)
    judge = build_chat_model(ModelRole.JUDGE, settings)
    assert isinstance(guard, ChatOllama)
    assert isinstance(judge, ChatOllama)
    assert guard.num_predict == 256
    assert guard.client_kwargs == {"timeout": 30.0}
    assert judge.model == "gpt-oss:20b"


def test_custom_ollama_url_is_used(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DESKPILOT_OLLAMA_BASE_URL", "http://ollama.internal:9999")
    model = build_chat_model(ModelRole.AGENT, load())
    assert isinstance(model, ChatOllama)
    assert model.base_url == "http://ollama.internal:9999"


def test_anthropic_agent_receives_configured_parameters() -> None:
    settings = load(anthropic_api_key="sk-ant-test", agent=anthropic_agent())
    model = build_chat_model(ModelRole.AGENT, settings)
    assert isinstance(model, ChatAnthropic)
    assert model.model == "claude-sonnet-5"
    assert model.anthropic_api_key.get_secret_value() == "sk-ant-test"
    assert model.max_tokens == 2048
    assert model.default_request_timeout == 45.0
    assert model.temperature == 0.0


def test_roles_switch_provider_independently() -> None:
    settings = load(anthropic_api_key="sk-ant-test", agent=anthropic_agent())
    assert isinstance(build_chat_model(ModelRole.AGENT, settings), ChatAnthropic)
    assert isinstance(build_chat_model(ModelRole.GUARD, settings), ChatOllama)


def test_api_key_is_not_exposed_by_the_built_model() -> None:
    settings = load(anthropic_api_key="sk-ant-test", agent=anthropic_agent())
    model = build_chat_model(ModelRole.AGENT, settings)
    assert "sk-ant-test" not in repr(model)
