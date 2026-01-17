"""Secret loading utilities."""

from __future__ import annotations

import structlog
from pathlib import Path

from google.cloud import secretmanager
from google.oauth2 import service_account

from ace.config.settings import Settings

logger = structlog.get_logger(__name__)


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
    return bool(settings.gcp_project_id)


def resolve_github_token(settings: Settings) -> str:
    """Resolve the GitHub token, preferring Secret Manager when available."""
    token = ""
    if _should_use_secret_manager(settings, settings.github_token_secret_name):
        try:
            token = load_secret(
                settings.gcp_project_id,
                settings.github_token_secret_name,
                settings.github_token_secret_version,
                settings.gcp_credentials_path,
            )
        except Exception as e:
            logger.warning("github_token_secret_failed", error=str(e))

    if not token:
        token = settings.github_token

    if not token:
        raise ValueError(
            "GitHub token missing. Set GITHUB_CONTROL_API_KEY or configure Secret Manager."
        )
    return token


def resolve_langsmith_api_key(settings: Settings) -> str:
    """Resolve the LangSmith API key, preferring Secret Manager when available."""
    api_key = ""
    if _should_use_secret_manager(settings, settings.langsmith_secret_name):
        try:
            api_key = load_secret(
                settings.gcp_project_id,
                settings.langsmith_secret_name,
                settings.langsmith_secret_version,
                settings.gcp_credentials_path,
            )
        except Exception as e:
            logger.warning("langsmith_secret_failed", error=str(e))
    return api_key or settings.langsmith_api_key
