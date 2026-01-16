"""Manage issue status and agent label transitions."""

from enum import Enum

import structlog

from ace.config.settings import get_settings

logger = structlog.get_logger(__name__)


class IssueStatus(str, Enum):
    """Project status values for issues."""

    READY = "Ready"
    IN_PROGRESS = "In Progress"
    BLOCKED = "Blocked"
    DONE = "Done"


class StatusManager:
    """Manages issue status and agent label transitions."""

    def __init__(self, issue_queue):
        """Initialize status manager.

        Args:
            issue_queue: IssueQueue instance for API calls
        """
        self.issue_queue = issue_queue
        self.settings = get_settings()

    async def claim_issue(self, issue_number: int, repo: str, branch: str) -> None:
        """Claim an issue: set status to In Progress, keep agent label.

        Args:
            issue_number: GitHub issue number
            repo: Repository name
            branch: Branch name
        """
        logger.info("claiming_issue", issue=issue_number, repo=repo, branch=branch)

        claim_comment = f"""**Agent Claim**

- Status: In Progress
- Repository: {repo}
- Branch: {branch}
- Started: {self._get_timestamp()}
- Heartbeat: Updates posted at major milestones
"""
        await self.issue_queue.post_comment(issue_number, claim_comment)
        await self.issue_queue.set_project_status(issue_number, IssueStatus.IN_PROGRESS)
        logger.info("issue_claimed", issue=issue_number)

    async def mark_blocked(
        self,
        issue_number: int,
        questions: list[str],
        assignee: str = "kristinday",
    ) -> None:
        """Block issue: remove agent label, assign to user, post questions.

        Args:
            issue_number: GitHub issue number
            questions: List of questions for the user
            assignee: GitHub username to assign to
        """
        logger.info("marking_blocked", issue=issue_number, questions=questions)

        blocked_comment = "**BLOCKED - Agent Needs Input**\n\n"
        for i, question in enumerate(questions, 1):
            blocked_comment += f"{i}. {question}\n"
        blocked_comment += (
            "\nPlease reply with your answers and re-add the `agent` label when ready to resume."
        )

        await self.issue_queue.remove_labels(issue_number, [self.settings.github_agent_label])
        await self.issue_queue.assign_issue(issue_number, assignee)
        await self.issue_queue.post_comment(issue_number, blocked_comment)
        await self.issue_queue.set_project_status(issue_number, IssueStatus.BLOCKED)
        logger.info("issue_blocked", issue=issue_number, assignee=assignee)

    async def mark_done(
        self,
        issue_number: int,
        pr_number: int,
        pr_url: str,
    ) -> None:
        """Mark issue as done: set status to Done, remove agent label, post PR link.

        Args:
            issue_number: GitHub issue number
            pr_number: Pull request number
            pr_url: Pull request URL
        """
        logger.info("marking_done", issue=issue_number, pr=pr_number)

        done_comment = f"""**Agent Complete**

PR: #{pr_number}
URL: {pr_url}

Status: Done
"""
        await self.issue_queue.remove_labels(issue_number, [self.settings.github_agent_label])
        await self.issue_queue.post_comment(issue_number, done_comment)
        await self.issue_queue.set_project_status(issue_number, IssueStatus.DONE)
        logger.info("issue_marked_done", issue=issue_number, pr=pr_number)

    async def mark_failed(
        self,
        issue_number: int,
        error: str,
    ) -> None:
        """Mark issue as failed: set status to Blocked, remove agent label, post error.

        Args:
            issue_number: GitHub issue number
            error: Error message
        """
        logger.info("marking_failed", issue=issue_number, error=error)

        failed_comment = f"""**Agent Failed**

Error:
```
{error}
```

Status: Blocked - Please review and re-add the `agent` label to retry.
"""
        await self.issue_queue.remove_labels(issue_number, [self.settings.github_agent_label])
        await self.issue_queue.post_comment(issue_number, failed_comment)
        await self.issue_queue.set_project_status(issue_number, IssueStatus.BLOCKED)
        logger.info("issue_marked_failed", issue=issue_number)

    async def resume_from_blocked(self, issue_number: int) -> None:
        """Resume blocked issue: add agent label, set status to In Progress.

        Called when user re-adds agent label and provides answer.

        Args:
            issue_number: GitHub issue number
        """
        logger.info("resuming_from_blocked", issue=issue_number)

        resume_comment = "**Agent Resuming**\n\nContinuing with provided answers."
        await self.issue_queue.add_labels(issue_number, [self.settings.github_agent_label])
        await self.issue_queue.post_comment(issue_number, resume_comment)
        await self.issue_queue.set_project_status(issue_number, IssueStatus.IN_PROGRESS)
        logger.info("issue_resumed", issue=issue_number)

    def _get_timestamp(self) -> str:
        """Get current timestamp in ISO format."""
        from datetime import datetime

        return datetime.utcnow().isoformat()
