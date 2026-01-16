"""GitHub issue queue operations via MCP."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import structlog

from .mcp_client import GitHubMCPClient

logger = structlog.get_logger(__name__)


@dataclass
class Issue:
    """Represents a GitHub issue."""

    number: int
    title: str
    body: str
    labels: list[str]
    assignee: str | None
    state: str
    created_at: datetime
    updated_at: datetime
    html_url: str


class IssueQueue:
    """Manages issue queue operations via GitHub MCP."""

    def __init__(self, mcp_client: GitHubMCPClient, owner: str, repo: str):
        """Initialize the issue queue.

        Args:
            mcp_client: GitHub MCP client instance
            owner: Repository owner
            repo: Repository name
        """
        self.mcp_client = mcp_client
        self.owner = owner
        self.repo = repo

    async def list_issues_by_label(self, label: str, state: str = "open") -> list[Issue]:
        """List issues with a specific label.

        Args:
            label: Label to filter by
            state: Issue state (open, closed, all)

        Returns:
            List of issues matching the criteria
        """
        logger.info("listing_issues_by_label", label=label, state=state)
        result = await self.mcp_client.call_tool(
            "search_issues",
            {
                "query": f"repo:{self.owner}/{self.repo} label:{label} state:{state}",
            },
        )
        issues = []
        for item in result.get("items", []):
            issues.append(self._parse_issue(item))
        logger.info("issues_listed", count=len(issues))
        return issues

    async def claim_issue(self, issue_number: int, claim_comment: str) -> None:
        """Claim an issue by adding labels and a comment.

        Args:
            issue_number: Issue number to claim
            claim_comment: Comment to post when claiming
        """
        logger.info("claiming_issue", issue=issue_number)
        await self.add_labels(issue_number, ["agent:in-progress"])
        await self.post_comment(issue_number, claim_comment)
        logger.info("issue_claimed", issue=issue_number)

    async def post_comment(self, issue_number: int, body: str) -> dict[str, Any]:
        """Post a comment on an issue.

        Args:
            issue_number: Issue number
            body: Comment body

        Returns:
            Comment data
        """
        logger.info("posting_comment", issue=issue_number, body_length=len(body))
        result = await self.mcp_client.call_tool(
            "create_issue_comment",
            {
                "owner": self.owner,
                "repo": self.repo,
                "issue_number": issue_number,
                "body": body,
            },
        )
        logger.info("comment_posted", issue=issue_number)
        return result

    async def add_labels(self, issue_number: int, labels: list[str]) -> None:
        """Add labels to an issue.

        Args:
            issue_number: Issue number
            labels: Labels to add
        """
        logger.info("adding_labels", issue=issue_number, labels=labels)
        await self.mcp_client.call_tool(
            "add_issue_labels",
            {
                "owner": self.owner,
                "repo": self.repo,
                "issue_number": issue_number,
                "labels": labels,
            },
        )
        logger.info("labels_added", issue=issue_number)

    async def remove_labels(self, issue_number: int, labels: list[str]) -> None:
        """Remove labels from an issue.

        Args:
            issue_number: Issue number
            labels: Labels to remove
        """
        logger.info("removing_labels", issue=issue_number, labels=labels)
        await self.mcp_client.call_tool(
            "remove_issue_labels",
            {
                "owner": self.owner,
                "repo": self.repo,
                "issue_number": issue_number,
                "labels": labels,
            },
        )
        logger.info("labels_removed", issue=issue_number)

    async def assign_issue(self, issue_number: int, assignee: str) -> None:
        """Assign an issue to a user.

        Args:
            issue_number: Issue number
            assignee: GitHub username to assign to
        """
        logger.info("assigning_issue", issue=issue_number, assignee=assignee)
        await self.mcp_client.call_tool(
            "update_issue",
            {
                "owner": self.owner,
                "repo": self.repo,
                "issue_number": issue_number,
                "assignee": assignee,
            },
        )
        logger.info("issue_assigned", issue=issue_number, assignee=assignee)

    async def create_pull_request(
        self,
        title: str,
        body: str,
        head: str,
        base: str = "main",
    ) -> dict[str, Any]:
        """Create a pull request.

        Args:
            title: PR title
            body: PR description
            head: Head branch
            base: Base branch (default: main)

        Returns:
            PR data
        """
        logger.info("creating_pull_request", title=title, head=head, base=base)
        result = await self.mcp_client.call_tool(
            "create_pull_request",
            {
                "owner": self.owner,
                "repo": self.repo,
                "title": title,
                "body": body,
                "head": head,
                "base": base,
            },
        )
        logger.info("pull_request_created", pr_number=result.get("number"))
        return result

    def _parse_issue(self, item: dict[str, Any]) -> Issue:
        """Parse a GitHub API issue response into an Issue object."""
        return Issue(
            number=item["number"],
            title=item["title"],
            body=item.get("body", ""),
            labels=[label["name"] for label in item.get("labels", [])],
            assignee=item.get("assignee", {}).get("login") if item.get("assignee") else None,
            state=item["state"],
            created_at=datetime.fromisoformat(item["created_at"].replace("Z", "+00:00")),
            updated_at=datetime.fromisoformat(item["updated_at"].replace("Z", "+00:00")),
            html_url=item["html_url"],
        )
