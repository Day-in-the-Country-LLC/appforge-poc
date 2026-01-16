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
    github_token: str = os.getenv("GITHUB_CONTROL_API_KEY", "")
    github_org: str = os.getenv("GITHUB_ORG", "Day-in-the-Country-LLC")
    github_project_name: str = os.getenv("GITHUB_PROJECT_NAME", "DITC TODO")
    github_webhook_secret: Optional[str] = os.getenv("GITHUB_WEBHOOK_SECRET")
    github_ready_status: str = os.getenv("GITHUB_READY_STATUS", "Ready")
    github_agent_label: str = os.getenv("GITHUB_AGENT_LABEL", "agent")
    github_local_agent_label: str = os.getenv("GITHUB_LOCAL_AGENT_LABEL", "agent:local")
    github_remote_agent_label: str = os.getenv("GITHUB_REMOTE_AGENT_LABEL", "agent:remote")
    github_base_branch: str = os.getenv("GITHUB_BASE_BRANCH", "main")
    github_token_secret_name: str = os.getenv("GITHUB_TOKEN_SECRET_NAME", "")
    github_token_secret_version: str = os.getenv("GITHUB_TOKEN_SECRET_VERSION", "latest")
    github_mcp_token_env: str = os.getenv("GITHUB_MCP_TOKEN_ENV", "GITHUB_TOKEN")

    # OpenAI / Codex
    openai_api_key: str = os.getenv("APPFORGE_OPENAI_API_KEY", "")
    codex_model: str = os.getenv("CODEX_MODEL", "gpt-5.1-codex-mini")

    # Claude
    claude_api_key: str = os.getenv("CLAUDE_CODE_ADMIN_API_KEY", "")
    claude_model: str = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5")

    # GCP
    gcp_project_id: str = os.getenv("GCP_PROJECT_ID", "")
    gcp_secret_manager_enabled: bool = (
        os.getenv("GCP_SECRET_MANAGER_ENABLED", "false").lower() == "true"
    )

    # Agent workspace
    agent_workspace_root: str = os.getenv("AGENT_WORKSPACE_ROOT", "/tmp/agent-hq")
    agent_id: str = os.getenv("AGENT_ID", "ace-default")
    agent_execution_mode: str = os.getenv("AGENT_EXECUTION_MODE", "tmux")

    # CLI agent commands
    codex_cli_command: str = os.getenv("CODEX_CLI_COMMAND", "codex --model {model}")
    claude_cli_command: str = os.getenv("CLAUDE_CLI_COMMAND", "claude --model {model}")

    # Service
    service_port: int = int(os.getenv("SERVICE_PORT", "8080"))
    service_host: str = os.getenv("SERVICE_HOST", "0.0.0.0")

    # Polling
    polling_interval_seconds: int = int(os.getenv("POLLING_INTERVAL_SECONDS", "60"))

    # Difficulty-based model mapping
    difficulty_easy_backend: str = os.getenv("DIFFICULTY_EASY_BACKEND", "codex")
    difficulty_easy_model: str = os.getenv("DIFFICULTY_EASY_MODEL", "gpt-5.1-codex-mini")
    difficulty_medium_backend: str = os.getenv("DIFFICULTY_MEDIUM_BACKEND", "claude")
    difficulty_medium_model: str = os.getenv("DIFFICULTY_MEDIUM_MODEL", "claude-haiku-4-5")
    difficulty_hard_backend: str = os.getenv("DIFFICULTY_HARD_BACKEND", "claude")
    difficulty_hard_model: str = os.getenv("DIFFICULTY_HARD_MODEL", "claude-opus-4-5")

    # Blocked handling
    blocked_assignee: str = os.getenv("BLOCKED_ASSIGNEE", "kristinday")

    # Twilio SMS notifications
    twilio_enabled: bool = os.getenv("TWILIO_ENABLED", "false").lower() == "true"
    twilio_account_sid: str = os.getenv("TWILIO_ACCOUNT_SID", "")
    twilio_auth_token: str = os.getenv("TWILIO_AUTH_TOKEN", "")
    twilio_messaging_service_sid: str = os.getenv("TWILIO_MESSAGING_SERVICE_SID", "")
    twilio_to_number: str = os.getenv("TWILIO_TO_NUMBER", "")

    class Config:
        env_file = ".env"
        case_sensitive = False


def get_settings() -> Settings:
    """Get or create the global settings instance."""
    return Settings()
