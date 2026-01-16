"""Configuration and settings management."""

import os
from typing import Optional

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment and GCP Secret Manager."""

    # Environment
    environment: str = os.getenv("ENVIRONMENT", "development")
    debug: bool = environment == "development"

    # GitHub
    github_token: str = os.getenv("GITHUB_TOKEN", "")
    github_repo_owner: str = os.getenv("GITHUB_REPO_OWNER", "")
    github_repo_name: str = os.getenv("GITHUB_REPO_NAME", "")
    github_webhook_secret: Optional[str] = os.getenv("GITHUB_WEBHOOK_SECRET")

    # OpenAI / Codex
    openai_api_key: str = os.getenv("APPFORGE_OPENAI_API_KEY", "")
    codex_model: str = os.getenv("CODEX_MODEL", "gpt-4")

    # Claude
    claude_api_key: str = os.getenv("CLAUDE_CODE_ADMIN_API_KEY", "")

    # GCP
    gcp_project_id: str = os.getenv("GCP_PROJECT_ID", "")
    gcp_secret_manager_enabled: bool = (
        os.getenv("GCP_SECRET_MANAGER_ENABLED", "false").lower() == "true"
    )

    # Agent workspace
    agent_workspace_root: str = os.getenv("AGENT_WORKSPACE_ROOT", "/tmp/agent-hq")
    agent_id: str = os.getenv("AGENT_ID", "ace-default")

    # Service
    service_port: int = int(os.getenv("SERVICE_PORT", "8080"))
    service_host: str = os.getenv("SERVICE_HOST", "0.0.0.0")

    # Polling
    polling_interval_seconds: int = int(os.getenv("POLLING_INTERVAL_SECONDS", "60"))

    class Config:
        env_file = ".env"
        case_sensitive = False


def get_settings() -> Settings:
    """Get or create the global settings instance."""
    return Settings()
