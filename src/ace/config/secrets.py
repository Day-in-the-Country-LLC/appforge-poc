"""Secret loading utilities."""

from __future__ import annotations

import structlog
from google.cloud import secretmanager

from ace.config.settings import Settings

logger = structlog.get_logger(__name__)


def load_secret(
    project_id: str,
    secret_name: str,
    version: str = "latest",
) -> str:
    """Load a secret from GCP Secret Manager."""
    client = secretmanager.SecretManagerServiceClient()
    secret_path = f"projects/{project_id}/secrets/{secret_name}/versions/{version}"
    response = client.access_secret_version(name=secret_path)
    return response.payload.data.decode("UTF-8")


def resolve_github_token(settings: Settings) -> str:
    """Resolve the GitHub token, preferring Secret Manager when enabled."""
    if settings.gcp_secret_manager_enabled and settings.github_token_secret_name:
        try:
            return load_secret(
                settings.gcp_project_id,
                settings.github_token_secret_name,
                settings.github_token_secret_version,
            )
        except Exception as e:
            logger.warning("github_token_secret_failed", error=str(e))
    return settings.github_token
