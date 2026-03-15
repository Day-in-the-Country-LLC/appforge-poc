"""Configuration and settings management."""

import os

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment and GCP Secret Manager."""

    # Secrets backend (override via CLI arg)
    secrets_backend: str = "secret-manager"

    # Environment
    debug: bool = os.getenv("DEBUG", "false").lower() == "true"

    # GitHub
    github_token: str = os.getenv("GITHUB_TOKEN", "")
    github_org: str = os.getenv("GITHUB_ORG", "your-org")
    github_project_name: str = os.getenv("GITHUB_PROJECT_NAME", "your-project")
    github_ready_status: str = os.getenv("GITHUB_READY_STATUS", "Ready")
    github_agent_label: str = os.getenv("GITHUB_AGENT_LABEL", "agent")
    github_local_agent_label: str = os.getenv("GITHUB_LOCAL_AGENT_LABEL", "agent:local")
    github_remote_agent_label: str = os.getenv("GITHUB_REMOTE_AGENT_LABEL", "agent:remote")
    disable_issue_status: bool = os.getenv("DISABLE_ISSUE_STATUS", "false").lower() == "true"
    github_token_secret_name: str = os.getenv("GITHUB_TOKEN_SECRET_NAME", "github-control-api-key")
    github_token_secret_version: str = os.getenv("GITHUB_TOKEN_SECRET_VERSION", "latest")
    github_mcp_token_env: str = os.getenv("GITHUB_MCP_TOKEN_ENV", "GITHUB_TOKEN")
    mcp_config_filename: str = os.getenv("MCP_CONFIG_FILENAME", ".mcp.json")
    mcp_server_name: str = os.getenv("MCP_SERVER_NAME", "github")
    claude_mcp_url: str = os.getenv("CLAUDE_MCP_URL", "https://api.githubcopilot.com/mcp")
    codex_mcp_url: str = os.getenv("CODEX_MCP_URL", "https://api.githubcopilot.com/mcp/")
    codex_config_path: str = os.getenv("CODEX_CONFIG_PATH", "~/.codex/config.toml")
    # Appforge MCP (enabled when URL is provided)
    appforge_mcp_url: str = os.getenv("APPFORGE_MCP_URL", "")
    appforge_mcp_server_name: str = os.getenv("APPFORGE_MCP_SERVER_NAME", "appforge-mcp-server")
    issue_tracker_backend: str = os.getenv("ISSUE_TRACKER_BACKEND", "github").lower()

    # Linear tracker integration
    linear_api_url: str = os.getenv("LINEAR_API_URL", "https://api.linear.app/graphql")
    linear_api_key: str = os.getenv("LINEAR_API_KEY", "")
    linear_api_key_secret_name: str = os.getenv("LINEAR_API_KEY_SECRET_NAME", "linear-api-key")
    linear_api_key_secret_version: str = os.getenv("LINEAR_API_KEY_SECRET_VERSION", "latest")
    linear_team_name: str = os.getenv("LINEAR_TEAM_NAME", "")
    linear_default_project_name: str = os.getenv("LINEAR_DEFAULT_PROJECT_NAME", "")
    linear_default_repo_owner: str = os.getenv("LINEAR_DEFAULT_REPO_OWNER", "")
    linear_default_repo_name: str = os.getenv("LINEAR_DEFAULT_REPO_NAME", "")
    linear_ready_status: str = os.getenv("LINEAR_READY_STATUS", "Ready")
    linear_in_progress_status: str = os.getenv("LINEAR_IN_PROGRESS_STATUS", "In Progress")
    linear_blocked_status: str = os.getenv("LINEAR_BLOCKED_STATUS", "Blocked")
    linear_done_status: str = os.getenv("LINEAR_DONE_STATUS", "Done")

    # OpenAI / Codex
    openai_api_key: str = os.getenv("APPFORGE_OPENAI_API_KEY", "")
    openai_secret_name: str = os.getenv("OPENAI_SECRET_NAME", "APPFORGE_OPENAI_API_KEY")
    openai_secret_version: str = os.getenv("OPENAI_SECRET_VERSION", "latest")
    codex_model: str = os.getenv("CODEX_MODEL", "gpt-5.1-codex-mini")
    instruction_backend: str = os.getenv("INSTRUCTION_BACKEND", "openai")
    instruction_model: str = os.getenv(
        "INSTRUCTION_MODEL", os.getenv("CODEX_MODEL", "gpt-5.2-codex")
    )

    # Claude
    claude_api_key: str = os.getenv("CLAUDE_CODE_ADMIN_API_KEY", "")
    claude_secret_name: str = os.getenv("CLAUDE_SECRET_NAME", "appforge-anthropic-api-key")
    claude_secret_version: str = os.getenv("CLAUDE_SECRET_VERSION", "latest")
    claude_model: str = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5")
    planning_review_claude_model: str = os.getenv("PLANNING_REVIEW_CLAUDE_MODEL", "claude-opus-4-6")
    planning_review_claude_max_tokens: int = int(
        os.getenv("PLANNING_REVIEW_CLAUDE_MAX_TOKENS", "1800")
    )
    planning_review_openai_model: str = os.getenv("PLANNING_REVIEW_OPENAI_MODEL", "gpt-5.2-codex")
    planning_review_openai_max_tokens: int = int(
        os.getenv("PLANNING_REVIEW_OPENAI_MAX_TOKENS", "3000")
    )
    planning_review_openai_reasoning_effort: str = os.getenv(
        "PLANNING_REVIEW_OPENAI_REASONING_EFFORT",
        "high",
    ).strip().lower()

    # GCP
    gcp_project_id: str = os.getenv("GCP_PROJECT_ID", "")
    gcp_credentials_path: str = os.getenv(
        "GCP_CREDENTIALS_FILE", os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")
    )

    # Agent workspace
    agent_workspace_root: str = os.getenv("AGENT_WORKSPACE_ROOT", "/tmp/agent-hq")
    agent_workspace_provider: str = os.getenv("AGENT_WORKSPACE_PROVIDER", "legacy")
    agent_project_session_key: str = os.getenv("AGENT_PROJECT_SESSION_KEY", "")
    agent_id: str = os.getenv("AGENT_ID", "ace-default")
    agent_execution_mode: str = os.getenv("AGENT_EXECUTION_MODE", "tmux")

    # Webhook service split (listener/worker)
    webhook_service_role: str = os.getenv("WEBHOOK_SERVICE_ROLE", "both").lower()
    webhook_pubsub_topic: str = os.getenv("WEBHOOK_PUBSUB_TOPIC", "")
    planning_pubsub_topic: str = os.getenv("PLANNING_PUBSUB_TOPIC", "")
    planning_artifacts_bucket: str = os.getenv(
        "PLANNING_ARTIFACTS_BUCKET",
        "appforge-planner-artifacts",
    )
    planner_api_token: str = os.getenv("PLANNER_API_TOKEN", "")
    repo_gcp_mapping_path: str = os.getenv("REPO_GCP_MAPPING_PATH", "docs/repo-gcp-mapping.json")
    planning_store_backend: str = os.getenv("PLANNING_STORE_BACKEND", "firestore").lower()
    planning_intake_agent_enabled: bool = (
        os.getenv("PLANNING_INTAKE_AGENT_ENABLED", "true").lower() == "true"
    )
    planning_intake_model: str = os.getenv("PLANNING_INTAKE_MODEL", "gpt-5.2-codex")
    planning_intake_max_tokens: int = int(os.getenv("PLANNING_INTAKE_MAX_TOKENS", "1400"))
    planning_intake_reasoning_effort: str = os.getenv(
        "PLANNING_INTAKE_REASONING_EFFORT",
        "high",
    ).strip().lower()
    planning_repo_scout_model: str = os.getenv("PLANNING_REPO_SCOUT_MODEL", "gpt-5.2-codex")
    planning_repo_scout_max_tokens: int = int(os.getenv("PLANNING_REPO_SCOUT_MAX_TOKENS", "1200"))
    planning_repo_scout_reasoning_effort: str = os.getenv(
        "PLANNING_REPO_SCOUT_REASONING_EFFORT",
        "medium",
    ).strip().lower()
    planning_synthesis_model: str = os.getenv("PLANNING_SYNTHESIS_MODEL", "gpt-5.2-codex")
    planning_synthesis_plan_max_tokens: int = int(
        os.getenv("PLANNING_SYNTHESIS_PLAN_MAX_TOKENS", "4000")
    )
    planning_synthesis_issue_max_tokens: int = int(
        os.getenv("PLANNING_SYNTHESIS_ISSUE_MAX_TOKENS", "12000")
    )
    planning_synthesis_dependencies_max_tokens: int = int(
        os.getenv("PLANNING_SYNTHESIS_DEPENDENCIES_MAX_TOKENS", "2000")
    )
    planning_synthesis_controller_max_tokens: int = int(
        os.getenv("PLANNING_SYNTHESIS_CONTROLLER_MAX_TOKENS", "2000")
    )
    planning_synthesis_reasoning_effort: str = os.getenv(
        "PLANNING_SYNTHESIS_REASONING_EFFORT",
        "high",
    ).strip().lower()
    planning_synthesis_max_turns_per_agent: int = int(
        os.getenv("PLANNING_SYNTHESIS_MAX_TURNS_PER_AGENT", "3")
    )
    planning_intake_max_repo_scouts_per_turn: int = int(
        os.getenv("PLANNING_INTAKE_MAX_REPO_SCOUTS_PER_TURN", "2")
    )
    planning_intake_max_repo_reports: int = int(os.getenv("PLANNING_INTAKE_MAX_REPO_REPORTS", "6"))
    pr_review_enabled: bool = os.getenv("PR_REVIEW_ENABLED", "true").lower() == "true"
    pr_review_pubsub_topic: str = os.getenv("PR_REVIEW_PUBSUB_TOPIC", "")
    pr_review_max_rounds: int = int(os.getenv("PR_REVIEW_MAX_ROUNDS", "2"))
    pr_review_consensus_mode: str = (
        os.getenv("PR_REVIEW_CONSENSUS_MODE", "strict").strip().lower()
    )
    pr_review_target_branch: str = os.getenv("PR_REVIEW_TARGET_BRANCH", "main")
    pr_review_require_checks: bool = (
        os.getenv("PR_REVIEW_REQUIRE_CHECKS", "true").lower() == "true"
    )
    pr_review_merge_method: str = os.getenv("PR_REVIEW_MERGE_METHOD", "squash")
    pr_review_codex_model: str = os.getenv("PR_REVIEW_CODEX_MODEL", "gpt-5.1-codex")
    pr_review_codex_max_tokens: int = int(os.getenv("PR_REVIEW_CODEX_MAX_TOKENS", "2500"))
    pr_review_codex_reasoning_effort: str = (
        os.getenv("PR_REVIEW_CODEX_REASONING_EFFORT", "high").strip().lower()
    )
    pr_review_claude_model: str = os.getenv("PR_REVIEW_CLAUDE_MODEL", "claude-haiku-4-5")
    pr_review_claude_max_tokens: int = int(os.getenv("PR_REVIEW_CLAUDE_MAX_TOKENS", "3000"))
    pr_review_diff_max_chars: int = int(os.getenv("PR_REVIEW_DIFF_MAX_CHARS", "8000"))
    pr_review_allowed_repos: str = os.getenv("PR_REVIEW_ALLOWED_REPOS", "")

    # CLI agent commands
    codex_cli_command: str = os.getenv(
        "CODEX_CLI_COMMAND",
        (
            "codex --ask-for-approval never --full-auto "
            "--sandbox danger-full-access --model {model} {prompt}"
        ),
    )
    claude_cli_command: str = os.getenv(
        "CLAUDE_CLI_COMMAND",
        # IMPORTANT: include `{prompt}` so the agent always receives an initial user prompt.
        # We still run in interactive mode (no `-p`), but avoid relying on tmux send-keys timing.
        "claude --permission-mode dontAsk --dangerously-skip-permissions --model {model} {prompt}",
    )
    cli_system_prompt_path: str = os.getenv(
        "CLI_SYSTEM_PROMPT_PATH", "prompts/cli_system_prompt.md"
    )

    # Task completion
    task_wait_timeout_seconds: int = int(os.getenv("TASK_WAIT_TIMEOUT_SECONDS", "900"))
    cleanup_enabled: bool = os.getenv("CLEANUP_ENABLED", "true").lower() == "true"
    cleanup_interval_seconds: int = int(os.getenv("CLEANUP_INTERVAL_SECONDS", "1800"))
    cleanup_worktree_retention_hours: int = int(os.getenv("CLEANUP_WORKTREE_RETENTION_HOURS", "72"))
    cleanup_tmux_retention_hours: int = int(os.getenv("CLEANUP_TMUX_RETENTION_HOURS", "12"))
    cleanup_only_done: bool = os.getenv("CLEANUP_ONLY_DONE", "true").lower() == "true"
    cleanup_tmux_enabled: bool = os.getenv("CLEANUP_TMUX_ENABLED", "true").lower() == "true"

    # Resume sweep
    resume_in_progress_issues: bool = (
        os.getenv("RESUME_IN_PROGRESS_ISSUES", "true").lower() == "true"
    )

    # Difficulty-based model mapping
    difficulty_easy_backend: str = os.getenv("DIFFICULTY_EASY_BACKEND", "claude")
    difficulty_easy_model: str = os.getenv("DIFFICULTY_EASY_MODEL", "claude-haiku-4-5")
    difficulty_medium_backend: str = os.getenv("DIFFICULTY_MEDIUM_BACKEND", "claude")
    difficulty_medium_model: str = os.getenv("DIFFICULTY_MEDIUM_MODEL", "claude-sonnet-4-5")
    difficulty_hard_backend: str = os.getenv("DIFFICULTY_HARD_BACKEND", "claude")
    difficulty_hard_model: str = os.getenv("DIFFICULTY_HARD_MODEL", "claude-opus-4-5")

    # Blocked handling
    blocked_assignee: str = os.getenv("BLOCKED_ASSIGNEE", "your-handle")

    # GitHub API retry/backoff
    github_api_max_retries: int = int(os.getenv("GITHUB_API_MAX_RETRIES", "5"))
    github_api_retry_base_seconds: float = float(os.getenv("GITHUB_API_RETRY_BASE_SECONDS", "1.0"))
    github_api_retry_max_seconds: float = float(os.getenv("GITHUB_API_RETRY_MAX_SECONDS", "30.0"))

    # Agent guidance
    claude_guide_path: str = os.getenv("CLAUDE_GUIDE_PATH", "~/.ace/CLAUDE.md")

    # Manager agent
    manager_agent_enabled: bool = True
    manager_agent_model: str = "gpt-5.1-codex-mini"
    manager_skill_path: str = "~/.codex/skills/appforge-manager-work-pickup/SKILL.md"
    manager_agent_tool_loop_enabled: bool = True
    manager_agent_tool_loop_max_steps: int = 6

    # LangSmith tracing
    langsmith_enabled: bool = os.getenv("LANGSMITH_ENABLED", "false").lower() == "true"
    langsmith_api_key: str = os.getenv("LANGSMITH_API_KEY", os.getenv("LANGCHAIN_API_KEY", ""))
    langsmith_secret_name: str = os.getenv(
        "LANGSMITH_SECRET_NAME", "LANGSMITH_ADS_OPTIMIZATION_KEY"
    )
    langsmith_secret_version: str = os.getenv("LANGSMITH_SECRET_VERSION", "latest")
    langsmith_project: str = os.getenv("LANGSMITH_PROJECT", os.getenv("LANGCHAIN_PROJECT", "ace"))
    langsmith_endpoint: str = os.getenv(
        "LANGSMITH_ENDPOINT",
        os.getenv("LANGCHAIN_ENDPOINT", "https://api.smith.langchain.com"),
    )
    langsmith_log_prompts: bool = os.getenv("LANGSMITH_LOG_PROMPTS", "true").lower() == "true"
    langsmith_log_responses: bool = os.getenv("LANGSMITH_LOG_RESPONSES", "true").lower() == "true"

    # Twilio SMS notifications
    twilio_enabled: bool = os.getenv("TWILIO_ENABLED", "false").lower() == "true"
    twilio_account_sid: str = os.getenv("TWILIO_ACCOUNT_SID", "")
    twilio_auth_token: str = os.getenv("TWILIO_AUTH_TOKEN", "")
    twilio_messaging_service_sid: str = os.getenv("TWILIO_MESSAGING_SERVICE_SID", "")
    twilio_to_number: str = os.getenv("TWILIO_TO_NUMBER", "")

    # Slack notifications (bot)
    slack_bot_token: str = os.getenv("SLACK_BOT_TOKEN", os.getenv("SLACKBOT_TOKEN", ""))
    slack_channel_id: str = os.getenv("SLACK_CHANNEL_ID", "")

    class Config:
        env_file = ".env"
        case_sensitive = False


_SETTINGS_OVERRIDES: dict[str, object] = {}


def set_settings_overrides(**kwargs: object) -> None:
    """Override settings via CLI args (preferred over env for flags)."""
    _SETTINGS_OVERRIDES.update(kwargs)


def get_settings() -> Settings:
    """Get or create the global settings instance."""
    return Settings(**_SETTINGS_OVERRIDES)
