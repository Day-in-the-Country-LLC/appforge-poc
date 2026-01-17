"""GitHub issue queue operations via REST API."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import structlog

from .api_client import GitHubAPIClient
from .projects_v2 import ProjectsV2Client

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
    repo_owner: str | None = None
    repo_name: str | None = None


class IssueQueue:
    """Manages issue queue operations via GitHub REST API."""

    def __init__(
        self,
        api_client: GitHubAPIClient,
        owner: str,
        repo: str,
        projects_client: ProjectsV2Client | None = None,
    ):
        """Initialize the issue queue.

        Args:
            api_client: GitHub API client instance
            owner: Repository owner (or org for cross-repo queries)
            repo: Repository name
            projects_client: Optional Projects V2 client for project board operations
        """
        self.api_client = api_client
        self.owner = owner
        self.repo = repo
        self.projects_client = projects_client
        self._project_id: str | None = None
        self._status_field_id: str | None = None
        self._status_options: dict[str, str] = {}

    async def list_issues_by_label(self, label: str, state: str = "open") -> list[Issue]:
        """List issues with a specific label.

        Args:
            label: Label to filter by
            state: Issue state (open, closed, all)

        Returns:
            List of issues matching the criteria
        """
        logger.info("listing_issues_by_label", label=label, state=state)
        result = await self.api_client.rest_get(
            "/search/issues",
            params={"q": f"repo:{self.owner}/{self.repo} label:{label} state:{state}"},
        )
        issues = []
        for item in result.get("items", []):
            issues.append(self._parse_issue(item))
        logger.info("issues_listed", count=len(issues))
        return issues

    async def list_issues_by_agent_label(self, agent_label: str) -> list[Issue]:
        """List issues with agent label (for org-wide queries).

        Args:
            agent_label: Agent label to filter by (e.g., "agent")

        Returns:
            List of issues matching the criteria
        """
        logger.info("listing_issues_by_agent_label", agent_label=agent_label)
        result = await self.api_client.rest_get(
            "/search/issues",
            params={"q": f"org:{self.owner} label:{agent_label} state:open"},
        )
        issues = []
        for item in result.get("items", []):
            issues.append(self._parse_issue(item))
        logger.info("issues_listed", count=len(issues))
        return issues

    async def list_issues_by_project_status(
        self,
        project_name: str,
        status: str,
    ) -> list[Issue]:
        """List issues from project board by status.

        Args:
            project_name: Name of the GitHub Project V2
            status: Status value to filter by (e.g., "Ready")

        Returns:
            List of issues matching the project status
        """
        if not self.projects_client:
            raise ValueError("ProjectsV2Client not configured")

        logger.info("listing_issues_by_project_status", project=project_name, status=status)

        if not self._project_id:
            self._project_id = await self.projects_client.get_org_project_id(
                self.owner, project_name
            )
            if not self._project_id:
                raise ValueError(f"Project '{project_name}' not found in org '{self.owner}'")

        project_items = await self.projects_client.list_project_items_by_status(
            self._project_id, status
        )

        issues = []
        for item in project_items:
            if item.content_type == "Issue":
                issues.append(
                    Issue(
                        number=item.number,
                        title=item.title,
                        body="",
                        labels=item.labels,
                        assignee=None,
                        state="open",
                        created_at=datetime.now(),
                        updated_at=datetime.now(),
                        html_url=item.html_url,
                        repo_owner=item.repo_owner,
                        repo_name=item.repo_name,
                    )
                )
        logger.info("issues_listed_from_project", count=len(issues))
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

    async def post_comment(
        self,
        issue_number: int,
        body: str,
        repo_owner: str | None = None,
        repo_name: str | None = None,
    ) -> dict[str, Any]:
        """Post a comment on an issue.

        Args:
            issue_number: Issue number
            body: Comment body
            repo_owner: Repository owner (defaults to self.owner)
            repo_name: Repository name (defaults to self.repo)

        Returns:
            Comment data
        """
        owner = repo_owner or self.owner
        repo = repo_name or self.repo
        logger.info("posting_comment", issue=issue_number, body_length=len(body))
        result = await self.api_client.rest_post(
            f"/repos/{owner}/{repo}/issues/{issue_number}/comments",
            json={"body": body},
        )
        logger.info("comment_posted", issue=issue_number)
        return result

    async def update_comment(
        self,
        comment_id: int,
        body: str,
        repo_owner: str | None = None,
        repo_name: str | None = None,
    ) -> dict[str, Any]:
        """Update an existing issue comment.

        Args:
            comment_id: Comment ID to update
            body: New comment body
            repo_owner: Repository owner (defaults to self.owner)
            repo_name: Repository name (defaults to self.repo)

        Returns:
            Updated comment data
        """
        owner = repo_owner or self.owner
        repo = repo_name or self.repo
        logger.info("updating_comment", comment_id=comment_id)
        result = await self.api_client.rest_patch(
            f"/repos/{owner}/{repo}/issues/comments/{comment_id}",
            json={"body": body},
        )
        logger.info("comment_updated", comment_id=comment_id)
        return result

    async def add_labels(
        self,
        issue_number: int,
        labels: list[str],
        repo_owner: str | None = None,
        repo_name: str | None = None,
    ) -> None:
        """Add labels to an issue.

        Args:
            issue_number: Issue number
            labels: Labels to add
            repo_owner: Repository owner (defaults to self.owner)
            repo_name: Repository name (defaults to self.repo)
        """
        owner = repo_owner or self.owner
        repo = repo_name or self.repo
        logger.info("adding_labels", issue=issue_number, labels=labels)
        await self.api_client.rest_post(
            f"/repos/{owner}/{repo}/issues/{issue_number}/labels",
            json={"labels": labels},
        )
        logger.info("labels_added", issue=issue_number)

    async def remove_labels(
        self,
        issue_number: int,
        labels: list[str],
        repo_owner: str | None = None,
        repo_name: str | None = None,
    ) -> None:
        """Remove labels from an issue.

        Args:
            issue_number: Issue number
            labels: Labels to remove
            repo_owner: Repository owner (defaults to self.owner)
            repo_name: Repository name (defaults to self.repo)
        """
        owner = repo_owner or self.owner
        repo = repo_name or self.repo
        logger.info("removing_labels", issue=issue_number, labels=labels)
        for label in labels:
            try:
                await self.api_client.rest_delete(
                    f"/repos/{owner}/{repo}/issues/{issue_number}/labels/{label}"
                )
            except Exception as e:
                logger.warning("label_removal_failed", label=label, error=str(e))
        logger.info("labels_removed", issue=issue_number)

    async def assign_issue(
        self,
        issue_number: int,
        assignee: str,
        repo_owner: str | None = None,
        repo_name: str | None = None,
    ) -> None:
        """Assign an issue to a user.

        Args:
            issue_number: Issue number
            assignee: GitHub username to assign to
            repo_owner: Repository owner (defaults to self.owner)
            repo_name: Repository name (defaults to self.repo)
        """
        owner = repo_owner or self.owner
        repo = repo_name or self.repo
        logger.info("assigning_issue", issue=issue_number, assignee=assignee)
        await self.api_client.rest_patch(
            f"/repos/{owner}/{repo}/issues/{issue_number}",
            json={"assignees": [assignee]},
        )
        logger.info("issue_assigned", issue=issue_number, assignee=assignee)

    async def create_pull_request(
        self,
        title: str,
        body: str,
        head: str,
        base: str = "main",
        repo_owner: str | None = None,
        repo_name: str | None = None,
    ) -> dict[str, Any]:
        """Create a pull request.

        Args:
            title: PR title
            body: PR description
            head: Head branch
            base: Base branch (default: main)
            repo_owner: Repository owner (defaults to self.owner)
            repo_name: Repository name (defaults to self.repo)

        Returns:
            PR data
        """
        owner = repo_owner or self.owner
        repo = repo_name or self.repo
        logger.info("creating_pull_request", title=title, head=head, base=base)
        result = await self.api_client.rest_post(
            f"/repos/{owner}/{repo}/pulls",
            json={
                "title": title,
                "body": body,
                "head": head,
                "base": base,
            },
        )
        logger.info("pull_request_created", pr_number=result.get("number"))
        return result

    async def get_issue(
        self,
        issue_number: int,
        repo_owner: str | None = None,
        repo_name: str | None = None,
    ) -> Issue:
        """Get a single issue by number.

        Args:
            issue_number: Issue number
            repo_owner: Repository owner (defaults to self.owner)
            repo_name: Repository name (defaults to self.repo)

        Returns:
            Issue object
        """
        owner = repo_owner or self.owner
        repo = repo_name or self.repo
        logger.info("getting_issue", issue=issue_number)
        result = await self.api_client.rest_get(f"/repos/{owner}/{repo}/issues/{issue_number}")
        return self._parse_issue(result, owner, repo)

    async def set_project_status(
        self,
        issue_number: int,
        status: str,
        project_name: str,
        repo_owner: str | None = None,
        repo_name: str | None = None,
    ) -> None:
        """Set project status for an issue.

        Args:
            issue_number: Issue number
            status: Status value (e.g., "Ready", "In Progress", "Blocked", "Done")
            project_name: Name of the GitHub Project V2
            repo_owner: Repository owner (defaults to self.owner)
            repo_name: Repository name (defaults to self.repo)
        """
        if not self.projects_client:
            raise ValueError("ProjectsV2Client not configured")

        owner = repo_owner or self.owner
        repo = repo_name or self.repo
        logger.info("setting_project_status", issue=issue_number, status=status)

        if not self._project_id:
            self._project_id = await self.projects_client.get_org_project_id(
                self.owner, project_name
            )
            if not self._project_id:
                raise ValueError(f"Project '{project_name}' not found")

        if not self._status_field_id:
            field_info = await self.projects_client.get_status_field_id(self._project_id)
            if not field_info:
                raise ValueError("Status field not found in project")
            self._status_field_id, self._status_options = field_info

        if status not in self._status_options:
            raise ValueError(
                f"Status '{status}' not found. Available: {list(self._status_options.keys())}"
            )

        item_id = await self.projects_client.get_item_id_for_issue(
            self._project_id, issue_number, owner, repo
        )
        if not item_id:
            raise ValueError(f"Issue #{issue_number} not found in project")

        await self.projects_client.update_item_status(
            self._project_id,
            item_id,
            self._status_field_id,
            self._status_options[status],
        )
        logger.info("project_status_set", issue=issue_number, status=status)

    def _parse_issue(
        self,
        item: dict[str, Any],
        repo_owner: str | None = None,
        repo_name: str | None = None,
    ) -> Issue:
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
            repo_owner=repo_owner,
            repo_name=repo_name,
        )
