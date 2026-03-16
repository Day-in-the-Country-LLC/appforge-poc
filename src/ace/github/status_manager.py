"""Manage issue status and agent label transitions."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum

import structlog

from ace.github.work_items import WorkItemEnricher, WorkItemTracker
from ace.config.settings import get_settings

logger = structlog.get_logger(__name__)


class IssueStatus(str, Enum):
    """Project status values for issues."""

    READY = "Ready"
    IN_PROGRESS = "In progress"
    BLOCKED = "Blocked"
    DONE = "Done"


class StatusManager:
    """Manages issue status and agent label transitions."""

    def __init__(self, tracker: WorkItemTracker, enricher: WorkItemEnricher) -> None:
        """Initialize status manager.

        Args:
            tracker: Tracker backend for status updates
            enricher: Enricher backend for comments/labels/assignee updates
        """
        self.tracker = tracker
        self.enricher = enricher
        self.settings = get_settings()
        self.status_disabled = self.settings.disable_issue_status
        self.backend = (self.settings.issue_tracker_backend or "github").lower()

    @property
    def project_name(self) -> str:
        if self.backend == "linear":
            return self.settings.linear_default_project_name
        return self.settings.github_project_name

    @property
    def status_ready(self) -> str:
        if self.backend == "linear" and self.settings.linear_ready_status:
            return self.settings.linear_ready_status
        return IssueStatus.READY.value

    @property
    def status_in_progress(self) -> str:
        if self.backend == "linear" and self.settings.linear_in_progress_status:
            return self.settings.linear_in_progress_status
        return IssueStatus.IN_PROGRESS.value

    @property
    def status_blocked(self) -> str:
        if self.backend == "linear" and self.settings.linear_blocked_status:
            return self.settings.linear_blocked_status
        return IssueStatus.BLOCKED.value

    @property
    def status_done(self) -> str:
        if self.backend == "linear" and self.settings.linear_done_status:
            return self.settings.linear_done_status
        return IssueStatus.DONE.value

    async def claim_issue(
        self,
        issue_number: int,
        repo_owner: str | None,
        repo_name: str | None,
        branch: str,
    ) -> None:
        """Claim an issue: set status to In Progress, keep agent label.

        Args:
            issue_number: GitHub issue number
            repo_owner: Repository owner
            repo_name: Repository name
            branch: Branch name
        """
        if not repo_owner or not repo_name:
            logger.warning("claim_issue_missing_repo", issue=issue_number)
            return
        if self.status_disabled:
            logger.info("claim_issue_skipped_status_disabled", issue=issue_number)
            return

        repo = f"{repo_owner}/{repo_name}"
        logger.info("claiming_issue", issue=issue_number, repo=repo, branch=branch)

        claim_comment = f"""**Agent Claim**

- Status: In Progress
- Repository: {repo}
- Branch: {branch}
- Started: {self._get_timestamp()}
- Heartbeat: Updates posted at major milestones
"""
        await self.enricher.post_comment(
            issue_number,
            claim_comment,
            repo_owner=repo_owner,
            repo_name=repo_name,
        )
        await self.tracker.set_issue_project_status(
            issue_number,
            self.status_in_progress,
            self.project_name,
            repo_owner=repo_owner,
            repo_name=repo_name,
        )
        logger.info("issue_claimed", issue=issue_number)

    async def mark_blocked(
        self,
        issue_number: int,
        questions: list[str],
        assignee: str | None = None,
        repo_owner: str | None = None,
        repo_name: str | None = None,
    ) -> None:
        """Block issue: remove agent label, assign to user, post questions.

        Args:
            issue_number: GitHub issue number
            questions: List of questions for the user
            assignee: GitHub username to assign to
        """
        if not repo_owner or not repo_name:
            logger.warning("mark_blocked_missing_repo", issue=issue_number)
            return
        if self.status_disabled:
            logger.info("mark_blocked_skipped_status_disabled", issue=issue_number)
            return

        logger.info("marking_blocked", issue=issue_number, questions=questions)

        blocked_comment = "**BLOCKED - Agent Needs Input**\n\n"
        for i, question in enumerate(questions, 1):
            blocked_comment += f"{i}. {question}\n"
        blocked_comment += (
            "\nPlease reply with your answers and re-add the `agent` label when ready to resume."
        )

        await self.enricher.remove_labels(
            issue_number,
            [self.settings.github_agent_label],
            repo_owner=repo_owner,
            repo_name=repo_name,
        )
        target_assignee = assignee or self.settings.blocked_assignee
        await self.enricher.assign_issue(
            issue_number,
            target_assignee,
            repo_owner=repo_owner,
            repo_name=repo_name,
        )
        await self.enricher.post_comment(
            issue_number,
            blocked_comment,
            repo_owner=repo_owner,
            repo_name=repo_name,
        )
        await self.tracker.set_issue_project_status(
            issue_number,
            self.status_blocked,
            self.project_name,
            repo_owner=repo_owner,
            repo_name=repo_name,
        )
        logger.info("issue_blocked", issue=issue_number, assignee=target_assignee)

    async def mark_blocked_from_comment(
        self,
        issue_number: int,
        repo_owner: str | None = None,
        repo_name: str | None = None,
    ) -> None:
        """Mark issue as blocked based on an existing BLOCKED comment."""
        if not repo_owner or not repo_name:
            logger.warning("mark_blocked_missing_repo", issue=issue_number)
            return
        if self.status_disabled:
            logger.info("mark_blocked_skipped_status_disabled", issue=issue_number)
            return

        logger.info("marking_blocked_from_comment", issue=issue_number)
        await self.tracker.set_issue_project_status(
            issue_number,
            self.status_blocked,
            self.project_name,
            repo_owner=repo_owner,
            repo_name=repo_name,
        )

    async def mark_done(
        self,
        issue_number: int,
        pr_number: int,
        pr_url: str,
        repo_owner: str | None = None,
        repo_name: str | None = None,
    ) -> None:
        """Mark issue as done: set status to Done, remove agent label, post PR link.

        Args:
            issue_number: GitHub issue number
            pr_number: Pull request number
            pr_url: Pull request URL
        """
        if not repo_owner or not repo_name:
            logger.warning("mark_done_missing_repo", issue=issue_number)
            return
        if self.status_disabled:
            logger.info("mark_done_skipped_status_disabled", issue=issue_number)
            return

        logger.info("marking_done", issue=issue_number, pr=pr_number)

        done_comment = f"""**Agent Complete**

PR: #{pr_number}
URL: {pr_url}

Status: Done
"""
        await self.enricher.remove_labels(
            issue_number,
            [self.settings.github_agent_label],
            repo_owner=repo_owner,
            repo_name=repo_name,
        )
        await self.enricher.post_comment(
            issue_number,
            done_comment,
            repo_owner=repo_owner,
            repo_name=repo_name,
        )
        await self.tracker.set_issue_project_status(
            issue_number,
            self.status_done,
            self.project_name,
            repo_owner=repo_owner,
            repo_name=repo_name,
        )
        logger.info("issue_marked_done", issue=issue_number, pr=pr_number)

    async def mark_failed(
        self,
        issue_number: int,
        error: str,
        repo_owner: str | None = None,
        repo_name: str | None = None,
    ) -> None:
        """Mark issue as failed: set status to Blocked, remove agent label, post error.

        Args:
            issue_number: GitHub issue number
            error: Error message
        """
        if not repo_owner or not repo_name:
            logger.warning("mark_failed_missing_repo", issue=issue_number)
            return
        if self.status_disabled:
            logger.info("mark_failed_skipped_status_disabled", issue=issue_number)
            return

        logger.info("marking_failed", issue=issue_number, error=error)

        failed_comment = f"""**Agent Failed**

Error:
```
{error}
```

Status: Blocked - Please review and re-add the `agent` label to retry.
"""
        await self.enricher.remove_labels(
            issue_number,
            [self.settings.github_agent_label],
            repo_owner=repo_owner,
            repo_name=repo_name,
        )
        await self.enricher.post_comment(
            issue_number,
            failed_comment,
            repo_owner=repo_owner,
            repo_name=repo_name,
        )
        await self.tracker.set_issue_project_status(
            issue_number,
            self.status_blocked,
            self.project_name,
            repo_owner=repo_owner,
            repo_name=repo_name,
        )
        logger.info("issue_marked_failed", issue=issue_number)

    async def resume_from_blocked(
        self,
        issue_number: int,
        repo_owner: str | None = None,
        repo_name: str | None = None,
    ) -> None:
        """Resume blocked issue: add agent label, set status to In Progress.

        Called when user re-adds agent label and provides answer.

        Args:
            issue_number: GitHub issue number
        """
        if not repo_owner or not repo_name:
            logger.warning("resume_missing_repo", issue=issue_number)
            return

        logger.info("resuming_from_blocked", issue=issue_number)

        resume_comment = "**Agent Resuming**\n\nContinuing with provided answers."
        await self.enricher.add_labels(
            issue_number,
            [self.settings.github_agent_label],
            repo_owner=repo_owner,
            repo_name=repo_name,
        )
        await self.enricher.post_comment(
            issue_number,
            resume_comment,
            repo_owner=repo_owner,
            repo_name=repo_name,
        )
        await self.tracker.set_issue_project_status(
            issue_number,
            self.status_in_progress,
            self.project_name,
            repo_owner=repo_owner,
            repo_name=repo_name,
        )
        logger.info("issue_resumed", issue=issue_number)

    def _get_timestamp(self) -> str:
        """Get current timestamp in ISO format."""
        return datetime.now(UTC).isoformat()
