"""LangGraph state definitions."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ace.agents.base import AgentResult
from ace.github.issue_queue import Issue


@dataclass
class WorkerState:
    """State for the agent worker graph."""

    # Issue metadata
    issue: Issue | None = None
    issue_number: int | None = None

    # Execution context
    agent_id: str = ""
    backend: str = "codex"  # "codex" or "claude"
    workspace_path: str = ""
    branch_name: str = ""

    # Execution tracking
    started_at: datetime | None = None
    last_update: datetime | None = None
    current_step: str = ""

    # Agent results
    plan: str = ""
    agent_result: AgentResult | None = None
    blocked_questions: list[str] = field(default_factory=list)

    # PR info
    pr_number: int | None = None
    pr_url: str | None = None

    # Claim comment info
    claim_comment_id: int | None = None

    # Error tracking
    error: str | None = None
    retry_count: int = 0

    # Metadata
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert state to dictionary for logging."""
        return {
            "issue_number": self.issue_number,
            "agent_id": self.agent_id,
            "backend": self.backend,
            "workspace_path": self.workspace_path,
            "branch_name": self.branch_name,
            "current_step": self.current_step,
            "pr_number": self.pr_number,
            "error": self.error,
            "retry_count": self.retry_count,
        }
