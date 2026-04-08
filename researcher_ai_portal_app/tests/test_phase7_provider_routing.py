from __future__ import annotations

import os

import pytest

from researcher_ai_portal_app import views


def test_provider_resolution_map():
    assert views._infer_provider("gpt-5.4") == "openai"
    assert views._infer_provider("o4-mini") == "openai"
    assert views._infer_provider("chatgpt-4o-latest") == "openai"
    assert views._infer_provider("claude-sonnet-4-6") == "anthropic"
    assert views._infer_provider("gemini-3.1-pro") == "gemini"


def test_api_key_validation_supports_provider_formats():
    assert views._validate_llm_api_key("sk-ant-12345678901234567890", "anthropic")
    assert views._validate_llm_api_key("sk-12345678901234567890", "openai")
    assert views._validate_llm_api_key("sk-proj-12345678901234567890", "openai")
    assert views._validate_llm_api_key("A" * 39, "gemini") == "A" * 39


def test_api_key_validation_rejects_invalid_gemini_key():
    with pytest.raises(ValueError, match="Gemini"):
        views._validate_llm_api_key("sk-this-is-not-gemini", "gemini")


@pytest.mark.parametrize(
    "provider,env_key",
    [
        ("openai", "OPENAI_API_KEY"),
        ("anthropic", "ANTHROPIC_API_KEY"),
        ("gemini", "GEMINI_API_KEY"),
    ],
)
def test_llm_env_sets_and_restores_provider_key(monkeypatch: pytest.MonkeyPatch, provider: str, env_key: str):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("RESEARCHER_AI_MODEL", raising=False)

    model = {
        "openai": "gpt-5.4",
        "anthropic": "claude-sonnet-4-6",
        "gemini": "gemini-3.1-pro",
    }[provider]

    job = {"llm_model": model, "llm_api_key": "X" * 39}
    if provider != "gemini":
        job["llm_api_key"] = "sk-ant-12345678901234567890" if provider == "anthropic" else "sk-12345678901234567890"

    with views._llm_env(job):
        assert os.environ.get("RESEARCHER_AI_MODEL") == model
        assert os.environ.get(env_key) == job["llm_api_key"]

    assert os.environ.get("RESEARCHER_AI_MODEL") is None
    assert os.environ.get("OPENAI_API_KEY") is None
    assert os.environ.get("ANTHROPIC_API_KEY") is None
    assert os.environ.get("GEMINI_API_KEY") is None
