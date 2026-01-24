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
    disable_issue_comments: bool = (
        os.getenv("DISABLE_ISSUE_COMMENTS", "false").lower() == "true"
    )
    disable_issue_status: bool = (
        os.getenv("DISABLE_ISSUE_STATUS", "false").lower() == "true"
    )
    github_base_branch: str = os.getenv("GITHUB_BASE_BRANCH", "main")
    github_token_secret_name: str = os.getenv(
        "GITHUB_TOKEN_SECRET_NAME", "github-control-api-key"
    )
    github_token_secret_version: str = os.getenv("GITHUB_TOKEN_SECRET_VERSION", "latest")
    github_mcp_token_env: str = os.getenv("GITHUB_MCP_TOKEN_ENV", "GITHUB_TOKEN")
    mcp_config_filename: str = os.getenv("MCP_CONFIG_FILENAME", ".mcp.json")
    mcp_server_name: str = os.getenv("MCP_SERVER_NAME", "github")
    claude_mcp_url: str = os.getenv(
        "CLAUDE_MCP_URL", "https://api.githubcopilot.com/mcp"
    )
    codex_mcp_url: str = os.getenv(
        "CODEX_MCP_URL", "https://api.githubcopilot.com/mcp/"
    )
    codex_config_path: str = os.getenv(
        "CODEX_CONFIG_PATH", "~/.codex/config.toml"
    )
    # Appforge MCP (hardcoded Cloud Run endpoint; no env toggle)
    appforge_mcp_enabled: bool = True
    appforge_mcp_url: str = "https://appforge-mcp-gchmaqkvia-uc.a.run.app"
    appforge_mcp_server_name: str = "appforge-mcp-server"

    # OpenAI / Codex
    openai_api_key: str = os.getenv("APPFORGE_OPENAI_API_KEY", "")
    openai_secret_name: str = os.getenv("OPENAI_SECRET_NAME", "APPFORGE_OPENAI_API_KEY")
    openai_secret_version: str = os.getenv("OPENAI_SECRET_VERSION", "latest")
    codex_model: str = os.getenv("CODEX_MODEL", "gpt-5.1-codex-mini")
    instruction_backend: str = os.getenv("INSTRUCTION_BACKEND", "openai")
    instruction_model: str = os.getenv(
        "INSTRUCTION_MODEL", os.getenv("CODEX_MODEL", "gpt-5.1-codex-mini")
    )

    # Claude
    claude_api_key: str = os.getenv("CLAUDE_CODE_ADMIN_API_KEY", "")
    claude_secret_name: str = os.getenv(
        "CLAUDE_SECRET_NAME", "appforge-anthropic-api-key"
    )
    claude_secret_version: str = os.getenv("CLAUDE_SECRET_VERSION", "latest")
    claude_model: str = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5")

    # GCP
    gcp_project_id: str = os.getenv("GCP_PROJECT_ID", "appforge-483920")
    gcp_credentials_path: str = os.getenv(
        "GCP_CREDENTIALS_FILE", os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")
    )

    # Agent workspace
    agent_workspace_root: str = os.getenv("AGENT_WORKSPACE_ROOT", "/tmp/agent-hq")
    agent_id: str = os.getenv("AGENT_ID", "ace-default")
    agent_execution_mode: str = os.getenv("AGENT_EXECUTION_MODE", "tmux")

    # CLI agent commands
    codex_cli_command: str = os.getenv(
        "CODEX_CLI_COMMAND",
        "codex --ask-for-approval never --full-auto --sandbox danger-full-access --model {model}",
    )
    claude_cli_command: str = os.getenv(
        "CLAUDE_CLI_COMMAND",
        "claude --permission-mode dontAsk --dangerously-skip-permissions --model {model}",
    )
    cli_system_prompt_path: str = os.getenv(
        "CLI_SYSTEM_PROMPT_PATH", "prompts/cli_system_prompt.md"
    )

    # Service
    service_port: int = int(os.getenv("SERVICE_PORT", "8080"))
    service_host: str = os.getenv("SERVICE_HOST", "0.0.0.0")

    # Polling
    polling_interval_seconds: int = int(os.getenv("POLLING_INTERVAL_SECONDS", "60"))

    # Task auto-advance (sequential tasks)
    task_auto_advance: bool = (
        os.getenv("TASK_AUTO_ADVANCE", "true").lower() == "true"
    )
    task_poll_interval_seconds: int = int(
        os.getenv("TASK_POLL_INTERVAL_SECONDS", "30")
    )
    task_wait_timeout_seconds: int = int(
        os.getenv("TASK_WAIT_TIMEOUT_SECONDS", "900")
    )
    task_nudge_enabled: bool = (
        os.getenv("TASK_NUDGE_ENABLED", "true").lower() == "true"
    )
    task_nudge_after_seconds: int = int(
        os.getenv("TASK_NUDGE_AFTER_SECONDS", "900")
    )
    task_nudge_interval_seconds: int = int(
        os.getenv("TASK_NUDGE_INTERVAL_SECONDS", "300")
    )
    task_nudge_max_attempts: int = int(
        os.getenv("TASK_NUDGE_MAX_ATTEMPTS", "3")
    )
    task_nudge_max_restarts: int = int(
        os.getenv("TASK_NUDGE_MAX_RESTARTS", "1")
    )
    task_nudge_message: str = os.getenv(
        "TASK_NUDGE_MESSAGE",
        "HEALTH_CHECK: please continue work on {task_id} ({task_title}). "
        "If blocked, post a BLOCKED comment and exit.",
    )
    cleanup_enabled: bool = os.getenv("CLEANUP_ENABLED", "true").lower() == "true"
    cleanup_interval_seconds: int = int(
        os.getenv("CLEANUP_INTERVAL_SECONDS", "1800")
    )
    cleanup_worktree_retention_hours: int = int(
        os.getenv("CLEANUP_WORKTREE_RETENTION_HOURS", "72")
    )
    cleanup_tmux_retention_hours: int = int(
        os.getenv("CLEANUP_TMUX_RETENTION_HOURS", "12")
    )
    cleanup_only_done: bool = os.getenv("CLEANUP_ONLY_DONE", "true").lower() == "true"
    cleanup_tmux_enabled: bool = (
        os.getenv("CLEANUP_TMUX_ENABLED", "true").lower() == "true"
    )

    # Resume sweep
    resume_in_progress_issues: bool = (
        os.getenv("RESUME_IN_PROGRESS_ISSUES", "true").lower() == "true"
    )

    # Difficulty-based model mapping
    difficulty_easy_backend: str = os.getenv("DIFFICULTY_EASY_BACKEND", "claude")
    difficulty_easy_model: str = os.getenv("DIFFICULTY_EASY_MODEL", "claude-haiku-4-5")
    difficulty_medium_backend: str = os.getenv("DIFFICULTY_MEDIUM_BACKEND", "claude")
    difficulty_medium_model: str = os.getenv(
        "DIFFICULTY_MEDIUM_MODEL", "claude-sonnet-4-5"
    )
    difficulty_hard_backend: str = os.getenv("DIFFICULTY_HARD_BACKEND", "claude")
    difficulty_hard_model: str = os.getenv("DIFFICULTY_HARD_MODEL", "claude-opus-4-5")

    # Blocked handling
    blocked_assignee: str = os.getenv("BLOCKED_ASSIGNEE", "kristinday")

    # GitHub API retry/backoff
    github_api_max_retries: int = int(os.getenv("GITHUB_API_MAX_RETRIES", "5"))
    github_api_retry_base_seconds: float = float(
        os.getenv("GITHUB_API_RETRY_BASE_SECONDS", "1.0")
    )
    github_api_retry_max_seconds: float = float(
        os.getenv("GITHUB_API_RETRY_MAX_SECONDS", "30.0")
    )

    # Agent guidance
    claude_guide_path: str = os.getenv("CLAUDE_GUIDE_PATH", "~/.ace/CLAUDE.md")

    # Manager agent
    manager_agent_enabled: bool = True
    manager_agent_model: str = os.getenv("MANAGER_AGENT_MODEL", "")
    manager_skill_path: str = "~/.codex/skills/appforge-manager-task-cleanup/SKILL.md"
    manager_agent_tool_loop_enabled: bool = True
    manager_agent_tool_loop_max_steps: int = 6

    # LangSmith tracing
    langsmith_enabled: bool = os.getenv("LANGSMITH_ENABLED", "false").lower() == "true"
    langsmith_api_key: str = os.getenv("LANGSMITH_API_KEY", os.getenv("LANGCHAIN_API_KEY", ""))
    langsmith_secret_name: str = os.getenv(
        "LANGSMITH_SECRET_NAME", "LANGSMITH_ADS_OPTIMIZATION_KEY"
    )
    langsmith_secret_version: str = os.getenv("LANGSMITH_SECRET_VERSION", "latest")
    langsmith_project: str = os.getenv(
        "LANGSMITH_PROJECT", os.getenv("LANGCHAIN_PROJECT", "ace")
    )
    langsmith_endpoint: str = os.getenv(
        "LANGSMITH_ENDPOINT",
        os.getenv("LANGCHAIN_ENDPOINT", "https://api.smith.langchain.com"),
    )
    langsmith_log_prompts: bool = (
        os.getenv("LANGSMITH_LOG_PROMPTS", "true").lower() == "true"
    )
    langsmith_log_responses: bool = (
        os.getenv("LANGSMITH_LOG_RESPONSES", "true").lower() == "true"
    )

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
