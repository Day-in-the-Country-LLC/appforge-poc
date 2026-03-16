"""Tests for secret resolution and caching behavior."""

from __future__ import annotations

from typing import Any

import pytest

from ace.config.secrets import clear_secret_cache, resolve_github_token, resolve_openai_api_key
from ace.config.secrets import _secret_cache_key
from ace.config.settings import Settings


def _build_secret_manager_settings(**overrides: Any) -> Settings:
    defaults = {
        "secrets_backend": "secret-manager",
        "gcp_project_id": "project-a",
        "github_token_secret_name": "github-token",
        "github_token_secret_version": "latest",
        "openai_secret_name": "openai-key",
        "openai_secret_version": "latest",
    }
    defaults.update(overrides)
    return Settings(**defaults)


def test_resolve_secret_caches_within_ttl(monkeypatch):
    clear_secret_cache()
    settings = _build_secret_manager_settings()
    calls: list[tuple[str, str, str]] = []

    def fake_load_secret(
        project_id: str,
        secret_name: str,
        version: str,
        credentials_path: str | None = None,
    ) -> str:
        calls.append((project_id, secret_name, version))
        return "cached-token"

    monkeypatch.setattr("ace.config.secrets.load_secret", fake_load_secret)

    first = resolve_github_token(settings)
    second = resolve_github_token(settings)

    assert first == "cached-token"
    assert second == "cached-token"
    assert calls == [("project-a", "github-token", "latest")]


def test_resolve_secret_cache_key_includes_project_secret_name_and_version():
    settings = _build_secret_manager_settings(gcp_project_id="project-a")

    assert _secret_cache_key(settings, "token", "latest") == ("project-a", "token", "latest")


def test_resolve_secret_different_keys_do_not_share_cache(monkeypatch):
    clear_secret_cache()
    calls: list[tuple[str, str]] = []

    def fake_load_secret(
        project_id: str,
        secret_name: str,
        version: str,
        credentials_path: str | None = None,
    ) -> str:
        calls.append((project_id, secret_name))
        return f"{project_id}:{secret_name}:{version}"

    monkeypatch.setattr("ace.config.secrets.load_secret", fake_load_secret)

    settings_a = _build_secret_manager_settings(gcp_project_id="project-a")
    settings_b = _build_secret_manager_settings(gcp_project_id="project-b")

    token_a_first = resolve_openai_api_key(settings_a)
    token_a_second = resolve_openai_api_key(settings_a)
    token_b = resolve_openai_api_key(settings_b)
    token_a_v2 = resolve_openai_api_key(
        _build_secret_manager_settings(gcp_project_id="project-a", openai_secret_version="v2")
    )

    assert token_a_first == "project-a:openai-key:latest"
    assert token_a_second == "project-a:openai-key:latest"
    assert token_b == "project-b:openai-key:latest"
    assert token_a_v2 == "project-a:openai-key:v2"
    assert calls == [
        ("project-a", "openai-key"),
        ("project-b", "openai-key"),
        ("project-a", "openai-key"),
    ]


def test_secret_cache_ttl_expires(monkeypatch):
    clear_secret_cache()
    current = {"now": 1000.0}
    monkeypatch.setattr("ace.config.secrets.time.time", lambda: current["now"])
    settings = _build_secret_manager_settings(secret_cache_ttl_seconds=2)
    calls: list[tuple[float, str]] = []

    def fake_load_secret(
        project_id: str,
        secret_name: str,
        version: str,
        credentials_path: str | None = None,
    ) -> str:
        calls.append((current["now"], secret_name))
        return "rotating-token"

    monkeypatch.setattr("ace.config.secrets.load_secret", fake_load_secret)

    assert resolve_openai_api_key(settings) == "rotating-token"
    assert resolve_openai_api_key(settings) == "rotating-token"
    assert len(calls) == 1

    current["now"] += 3

    assert resolve_openai_api_key(settings) == "rotating-token"
    assert len(calls) == 2


def test_env_backend_does_not_use_secret_manager_cache(monkeypatch):
    clear_secret_cache()
    settings = Settings(
        secrets_backend="env",
        gcp_project_id="project-a",
        github_token="env-token",
    )

    monkeypatch.setattr(
        "ace.config.secrets.load_secret",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("secret manager should not be called")),
    )

    token = resolve_github_token(settings)

    assert token == "env-token"
