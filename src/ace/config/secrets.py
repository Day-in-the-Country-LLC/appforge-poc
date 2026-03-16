"""Secret loading utilities."""

from __future__ import annotations

from pathlib import Path
import threading
import time

import structlog
from google.cloud import secretmanager
from google.oauth2 import service_account

from ace.config.settings import Settings

logger = structlog.get_logger(__name__)
_SECRET_CACHE: dict[tuple[str, str, str], tuple[str, float]] = {}
_SECRET_CACHE_LOCK = threading.Lock()


def _secret_backend(settings: Settings) -> str:
    return str(getattr(settings, "secrets_backend", "secret-manager")).lower()


def _secret_cache_key(
    settings: Settings,
    secret_name: str,
    version: str,
) -> tuple[str, str, str]:
    return (getattr(settings, "gcp_project_id", ""), secret_name, version)


def _get_cached_secret(key: tuple[str, str, str], ttl_seconds: int) -> str | None:
    if ttl_seconds <= 0:
        return None
    now = time.time()
    with _SECRET_CACHE_LOCK:
        cached = _SECRET_CACHE.get(key)
        if cached is None:
            return None
        value, fetched_at = cached
        if now - fetched_at >= ttl_seconds:
            _SECRET_CACHE.pop(key, None)
            return None
    return value


def _set_cached_secret(
    key: tuple[str, str, str],
    value: str,
) -> None:
    with _SECRET_CACHE_LOCK:
        _SECRET_CACHE[key] = (value, time.time())


def _resolve_secret(
    settings: Settings,
    *,
    secret_name: str,
    secret_version: str,
    settings_value: str,
    missing_message: str,
    not_configured_message: str,
    missing_secret_message: str,
    fetch_error_message: str,
) -> str:
    if _secret_backend(settings) == "env":
        if not settings_value:
            raise ValueError(f"❌ ERROR: {missing_message}")
        return settings_value

    if not _should_use_secret_manager(settings, secret_name):
        raise ValueError(f"❌ ERROR: {not_configured_message}")

    cache_key = _secret_cache_key(settings, secret_name, secret_version)
    ttl_seconds = int(getattr(settings, "secret_cache_ttl_seconds", 300))
    cached_secret = _get_cached_secret(cache_key, ttl_seconds)
    if cached_secret is not None:
        return cached_secret

    try:
        token = load_secret(
            getattr(settings, "gcp_project_id", ""),
            secret_name,
            secret_version,
            getattr(settings, "gcp_credentials_path", None),
        )
    except Exception as e:
        raise ValueError(f"❌ ERROR: {fetch_error_message} {e}") from e

    if not token:
        raise ValueError(f"❌ ERROR: {missing_secret_message}")

    _set_cached_secret(cache_key, token)
    return token


def clear_secret_cache() -> None:
    """Clear internal secret cache (mainly for tests)."""
    with _SECRET_CACHE_LOCK:
        _SECRET_CACHE.clear()


def load_secret(
    project_id: str,
    secret_name: str,
    version: str = "latest",
    credentials_path: str | None = None,
) -> str:
    """Load a secret from GCP Secret Manager."""
    credentials = _load_credentials(credentials_path)
    if credentials:
        client = secretmanager.SecretManagerServiceClient(credentials=credentials)
    else:
        client = secretmanager.SecretManagerServiceClient()
    secret_path = f"projects/{project_id}/secrets/{secret_name}/versions/{version}"
    response = client.access_secret_version(name=secret_path)
    return response.payload.data.decode("UTF-8").strip()


def _load_credentials(credentials_path: str | None) -> service_account.Credentials | None:
    path = credentials_path or ""
    if not path:
        fallback = Path("appforge-creds.json")
        if fallback.exists():
            path = str(fallback)
    if not path:
        return None

    try:
        return service_account.Credentials.from_service_account_file(path)
    except Exception as e:
        logger.warning("gcp_credentials_load_failed", path=path, error=str(e))
        return None


def _should_use_secret_manager(settings: Settings, secret_name: str) -> bool:
    if not secret_name:
        return False
    return bool(getattr(settings, "gcp_project_id", ""))


def _validate_backend(settings: Settings) -> None:
    backend = _secret_backend(settings)
    if backend not in ("secret-manager", "env"):
        raise ValueError(f"❌ ERROR: Unsupported secrets backend: {backend}")


def resolve_github_token(settings: Settings) -> str:
    """Resolve the GitHub token."""
    _validate_backend(settings)
    backend = _secret_backend(settings)
    if backend == "env":
        return _resolve_secret(
            settings,
            secret_name="",
            secret_version="latest",
            settings_value=getattr(settings, "github_token", ""),
            missing_message="GitHub token missing from environment",
            not_configured_message="GitHub token secret not configured",
            missing_secret_message="GitHub token missing from Secret Manager",
            fetch_error_message="GitHub secret fetch failed:",
        )
    return _resolve_secret(
        settings,
        secret_name=getattr(settings, "github_token_secret_name", "github-control-api-key"),
        secret_version=getattr(settings, "github_token_secret_version", "latest"),
        settings_value=getattr(settings, "github_token", ""),
        missing_message="GitHub token missing from environment",
        not_configured_message="GitHub token secret not configured",
        missing_secret_message="GitHub token missing from Secret Manager",
        fetch_error_message="GitHub secret fetch failed:",
    )


def resolve_langsmith_api_key(settings: Settings) -> str:
    """Resolve the LangSmith API key."""
    _validate_backend(settings)
    if not bool(getattr(settings, "langsmith_enabled", False)):
        return ""
    if _secret_backend(settings) == "env":
        return _resolve_secret(
            settings,
            secret_name="",
            secret_version="latest",
            settings_value=getattr(settings, "langsmith_api_key", ""),
            missing_message="LangSmith API key missing from environment",
            not_configured_message="LangSmith API key secret not configured",
            missing_secret_message="LangSmith API key missing from Secret Manager",
            fetch_error_message="LangSmith secret fetch failed:",
        )
    return _resolve_secret(
        settings,
        secret_name=getattr(
            settings,
            "langsmith_secret_name",
            "LANGSMITH_ADS_OPTIMIZATION_KEY",
        ),
        secret_version=getattr(settings, "langsmith_secret_version", "latest"),
        settings_value=getattr(settings, "langsmith_api_key", ""),
        missing_message="LangSmith API key missing from environment",
        not_configured_message="LangSmith API key secret not configured",
        missing_secret_message="LangSmith API key missing from Secret Manager",
        fetch_error_message="LangSmith secret fetch failed:",
    )


def resolve_openai_api_key(settings: Settings) -> str:
    """Resolve the OpenAI API key."""
    _validate_backend(settings)
    if _secret_backend(settings) == "env":
        return _resolve_secret(
            settings,
            secret_name="",
            secret_version="latest",
            settings_value=getattr(settings, "openai_api_key", ""),
            missing_message="OpenAI API key missing from environment",
            not_configured_message="OpenAI API key secret not configured",
            missing_secret_message="OpenAI API key missing from Secret Manager",
            fetch_error_message="OpenAI secret fetch failed:",
        )
    return _resolve_secret(
        settings,
        secret_name=getattr(settings, "openai_secret_name", "APPFORGE_OPENAI_API_KEY"),
        secret_version=getattr(settings, "openai_secret_version", "latest"),
        settings_value=getattr(settings, "openai_api_key", ""),
        missing_message="OpenAI API key missing from environment",
        not_configured_message="OpenAI API key secret not configured",
        missing_secret_message="OpenAI API key missing from Secret Manager",
        fetch_error_message="OpenAI secret fetch failed:",
    )


def resolve_claude_api_key(settings: Settings) -> str:
    """Resolve the Claude API key."""
    _validate_backend(settings)
    if _secret_backend(settings) == "env":
        return _resolve_secret(
            settings,
            secret_name="",
            secret_version="latest",
            settings_value=getattr(settings, "claude_api_key", ""),
            missing_message="Claude API key missing from environment",
            not_configured_message="Claude API key secret not configured",
            missing_secret_message="Claude API key missing from Secret Manager",
            fetch_error_message="Claude secret fetch failed:",
        )
    return _resolve_secret(
        settings,
        secret_name=getattr(settings, "claude_secret_name", "appforge-anthropic-api-key"),
        secret_version=getattr(settings, "claude_secret_version", "latest"),
        settings_value=getattr(settings, "claude_api_key", ""),
        missing_message="Claude API key missing from environment",
        not_configured_message="Claude API key secret not configured",
        missing_secret_message="Claude API key missing from Secret Manager",
        fetch_error_message="Claude secret fetch failed:",
    )


def resolve_linear_api_key(settings: Settings) -> str:
    """Resolve the Linear API key."""
    _validate_backend(settings)
    if _secret_backend(settings) == "env":
        return _resolve_secret(
            settings,
            secret_name="",
            secret_version="latest",
            settings_value=getattr(settings, "linear_api_key", ""),
            missing_message="Linear API key missing from environment",
            not_configured_message="Linear API key secret not configured",
            missing_secret_message="Linear API key missing from Secret Manager",
            fetch_error_message="Linear secret fetch failed:",
        )
    return _resolve_secret(
        settings,
        secret_name=getattr(settings, "linear_api_key_secret_name", "linear-api-key"),
        secret_version=getattr(settings, "linear_api_key_secret_version", "latest"),
        settings_value=getattr(settings, "linear_api_key", ""),
        missing_message="Linear API key missing from environment",
        not_configured_message="Linear API key secret not configured",
        missing_secret_message="Linear API key missing from Secret Manager",
        fetch_error_message="Linear secret fetch failed:",
    )
