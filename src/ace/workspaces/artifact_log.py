"""Artifact logging for issue execution."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class ArtifactLog:
    """Manages per-issue logs and artifacts."""

    def __init__(self, workspace_root: str):
        """Initialize artifact logger.

        Args:
            workspace_root: Root directory for artifacts
        """
        self.workspace_root = Path(workspace_root)
        self.logs_dir = self.workspace_root / "logs"
        self.logs_dir.mkdir(parents=True, exist_ok=True)

    def get_log_path(self, issue_number: int) -> Path:
        """Get the log file path for an issue.

        Args:
            issue_number: GitHub issue number

        Returns:
            Path to the log file
        """
        return self.logs_dir / f"issue-{issue_number}.jsonl"

    def log_event(
        self,
        issue_number: int,
        event_type: str,
        data: dict[str, Any],
    ) -> None:
        """Log an event for an issue.

        Args:
            issue_number: GitHub issue number
            event_type: Type of event
            data: Event data
        """
        log_path = self.get_log_path(issue_number)

        entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "event_type": event_type,
            **data,
        }

        try:
            with open(log_path, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            logger.error("log_write_failed", issue=issue_number, error=str(e))

    def log_step_start(self, issue_number: int, step_name: str) -> None:
        """Log the start of a workflow step.

        Args:
            issue_number: GitHub issue number
            step_name: Name of the step
        """
        self.log_event(issue_number, "step_start", {"step": step_name})

    def log_step_end(
        self,
        issue_number: int,
        step_name: str,
        status: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Log the end of a workflow step.

        Args:
            issue_number: GitHub issue number
            step_name: Name of the step
            status: Status (success, failed, blocked)
            metadata: Additional metadata
        """
        data = {"step": step_name, "status": status}
        if metadata:
            data.update(metadata)
        self.log_event(issue_number, "step_end", data)

    def log_agent_output(
        self,
        issue_number: int,
        output: str,
        files_changed: list[str],
        commands_run: list[str],
    ) -> None:
        """Log agent execution output.

        Args:
            issue_number: GitHub issue number
            output: Agent output text
            files_changed: List of files changed
            commands_run: List of commands executed
        """
        self.log_event(
            issue_number,
            "agent_output",
            {
                "output_length": len(output),
                "files_changed": files_changed,
                "commands_run": commands_run,
            },
        )

    def get_logs(self, issue_number: int) -> list[dict[str, Any]]:
        """Retrieve all logs for an issue.

        Args:
            issue_number: GitHub issue number

        Returns:
            List of log entries
        """
        log_path = self.get_log_path(issue_number)

        if not log_path.exists():
            return []

        logs = []
        try:
            with open(log_path, "r") as f:
                for line in f:
                    logs.append(json.loads(line))
        except Exception as e:
            logger.error("log_read_failed", issue=issue_number, error=str(e))

        return logs
