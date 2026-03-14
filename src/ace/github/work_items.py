"""High-level work item interfaces and GitHub-backed implementations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import structlog

from .api_client import GitHubAPIClient
from .issue_queue import Issue, IssueQueue
from .projects_v2 import BlockingIssue, ProjectItem, ProjectsV2Client

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class WorkBoardItem:
    """Canonical board-item shape exposed by work item trackers."""

    item_id: str
    content_id: str
    content_type: str
    title: str
    number: int
    repo_owner: str
    repo_name: str
    status: str | None
    labels: list[str]
    html_url: str


class WorkItemTracker(Protocol):
    """Interface for board-state-centric work item operations."""

    async def list_issues_by_project_status(
        self,
        project_name: str,
        status: str,
    ) -> list[Issue]:
        """List work items from a project board status."""

    async def get_issue(self, issue_number: int, repo_owner: str, repo_name: str) -> Issue:
        """Fetch a single issue."""

    async def get_project_id(self, project_name: str) -> str | None:
        """Resolve the org-level project node id."""

    async def get_project_item_by_id(self, item_id: str) -> WorkBoardItem | None:
        """Fetch a project item by its node id."""

    async def get_item_id_for_issue(
        self,
        project_id: str,
        issue_number: int,
        repo_owner: str,
        repo_name: str,
    ) -> str | None:
        """Find a project board item id for the given issue."""

    async def get_issue_project_status(
        self,
        issue_number: int,
        repo_owner: str,
        repo_name: str,
        project_name: str,
    ) -> str | None:
        """Fetch current project status for an issue."""

    async def set_issue_project_status(
        self,
        issue_number: int,
        status: str,
        project_name: str,
        *,
        repo_owner: str,
        repo_name: str,
    ) -> None:
        """Set the project status for an issue."""

    async def get_issue_blockers(
        self,
        repo_owner: str,
        repo_name: str,
        issue_number: int,
    ) -> list[BlockingIssue]:
        """Fetch issues blocking this issue."""


class WorkItemEnricher(Protocol):
    """Interface for issue-level enrichment operations."""

    async def post_comment(
        self,
        issue_number: int,
        body: str,
        *,
        repo_owner: str | None = None,
        repo_name: str | None = None,
    ) -> dict[str, Any]:
        """Post a comment to an issue."""

    async def update_comment(
        self,
        comment_id: int,
        body: str,
        *,
        repo_owner: str | None = None,
        repo_name: str | None = None,
    ) -> dict[str, Any]:
        """Update a comment on an issue."""

    async def add_labels(
        self,
        issue_number: int,
        labels: list[str],
        *,
        repo_owner: str | None = None,
        repo_name: str | None = None,
    ) -> None:
        """Add labels to an issue."""

    async def remove_labels(
        self,
        issue_number: int,
        labels: list[str],
        *,
        repo_owner: str | None = None,
        repo_name: str | None = None,
    ) -> None:
        """Remove labels from an issue."""

    async def assign_issue(
        self,
        issue_number: int,
        assignee: str,
        *,
        repo_owner: str | None = None,
        repo_name: str | None = None,
    ) -> None:
        """Assign an issue."""

    async def create_pull_request(
        self,
        title: str,
        body: str,
        head: str,
        base: str = "main",
        *,
        repo_owner: str | None = None,
        repo_name: str | None = None,
    ) -> dict[str, Any]:
        """Create a pull request for a repo."""


class GitHubWorkItemTracker(WorkItemTracker):
    """GitHub implementation of board-state work-item operations."""

    def __init__(
        self,
        api_client: GitHubAPIClient,
        owner: str,
        repo: str = "",
        projects_client: ProjectsV2Client | None = None,
        issue_queue: IssueQueue | None = None,
    ) -> None:
        self.api_client = api_client
        self.owner = owner
        self.repo = repo
        self._projects_client = projects_client
        self._issue_queue = issue_queue
        self._project_id_cache: dict[str, str] = {}

    @property
    def projects_client(self) -> ProjectsV2Client:
        if self._projects_client is None:
            self._projects_client = ProjectsV2Client(self.api_client)
        return self._projects_client

    @property
    def issue_queue(self) -> IssueQueue:
        if self._issue_queue is None:
            self._issue_queue = IssueQueue(
                self.api_client,
                self.owner,
                self.repo,
                self.projects_client,
            )
        return self._issue_queue

    async def list_issues_by_project_status(
        self,
        project_name: str,
        status: str,
    ) -> list[Issue]:
        return await self.issue_queue.list_issues_by_project_status(project_name, status)

    async def get_issue(
        self,
        issue_number: int,
        repo_owner: str,
        repo_name: str,
    ) -> Issue:
        return await self.issue_queue.get_issue(
            issue_number,
            repo_owner=repo_owner,
            repo_name=repo_name,
        )

    async def get_project_id(self, project_name: str) -> str | None:
        if not project_name.strip():
            return None
        if project_name in self._project_id_cache:
            return self._project_id_cache[project_name]

        project_id = await self.projects_client.get_org_project_id(self.owner, project_name)
        if project_id:
            self._project_id_cache[project_name] = project_id
            return project_id
        return None

    async def get_project_item_by_id(self, item_id: str) -> WorkBoardItem | None:
        item = await self.projects_client.get_project_item_by_id(item_id)
        return _to_work_board_item(item)

    async def get_item_id_for_issue(
        self,
        project_id: str,
        issue_number: int,
        repo_owner: str,
        repo_name: str,
    ) -> str | None:
        return await self.projects_client.get_item_id_for_issue(
            project_id,
            issue_number,
            repo_owner,
            repo_name,
        )

    async def get_issue_project_status(
        self,
        issue_number: int,
        repo_owner: str,
        repo_name: str,
        project_name: str,
    ) -> str | None:
        project_id = await self.get_project_id(project_name)
        if not project_id:
            raise ValueError(f"❌ ERROR: GitHub Project '{project_name}' not found")
        return await self.projects_client.get_issue_project_status(
            project_id,
            issue_number,
            repo_owner,
            repo_name,
        )

    async def set_issue_project_status(
        self,
        issue_number: int,
        status: str,
        project_name: str,
        *,
        repo_owner: str,
        repo_name: str,
    ) -> None:
        await self.issue_queue.set_project_status(
            issue_number,
            status,
            project_name,
            repo_owner=repo_owner,
            repo_name=repo_name,
        )

    async def get_issue_blockers(
        self,
        repo_owner: str,
        repo_name: str,
        issue_number: int,
    ) -> list[BlockingIssue]:
        try:
            return await self.projects_client.get_issue_blockers(
                repo_owner,
                repo_name,
                issue_number,
            )
        except Exception as exc:
            logger.warning(
                "work_item_tracker_fetch_blockers_failed",
                issue=issue_number,
                repo=f"{repo_owner}/{repo_name}",
                error=str(exc),
            )
            return []


class GitHubWorkItemEnricher(WorkItemEnricher):
    """GitHub implementation of issue enrichment operations."""

    def __init__(
        self,
        api_client: GitHubAPIClient,
        owner: str,
        repo: str = "",
        issue_queue: IssueQueue | None = None,
    ) -> None:
        self.api_client = api_client
        self.owner = owner
        self.repo = repo
        self._issue_queue = issue_queue

    @property
    def issue_queue(self) -> IssueQueue:
        if self._issue_queue is None:
            self._issue_queue = IssueQueue(self.api_client, self.owner, self.repo, None)
        return self._issue_queue

    async def post_comment(
        self,
        issue_number: int,
        body: str,
        *,
        repo_owner: str | None = None,
        repo_name: str | None = None,
    ) -> dict[str, Any]:
        return await self.issue_queue.post_comment(
            issue_number,
            body,
            repo_owner=repo_owner,
            repo_name=repo_name,
        )

    async def update_comment(
        self,
        comment_id: int,
        body: str,
        *,
        repo_owner: str | None = None,
        repo_name: str | None = None,
    ) -> dict[str, Any]:
        return await self.issue_queue.update_comment(
            comment_id,
            body,
            repo_owner=repo_owner,
            repo_name=repo_name,
        )

    async def add_labels(
        self,
        issue_number: int,
        labels: list[str],
        *,
        repo_owner: str | None = None,
        repo_name: str | None = None,
    ) -> None:
        await self.issue_queue.add_labels(
            issue_number,
            labels,
            repo_owner=repo_owner,
            repo_name=repo_name,
        )

    async def remove_labels(
        self,
        issue_number: int,
        labels: list[str],
        *,
        repo_owner: str | None = None,
        repo_name: str | None = None,
    ) -> None:
        await self.issue_queue.remove_labels(
            issue_number,
            labels,
            repo_owner=repo_owner,
            repo_name=repo_name,
        )

    async def assign_issue(
        self,
        issue_number: int,
        assignee: str,
        *,
        repo_owner: str | None = None,
        repo_name: str | None = None,
    ) -> None:
        await self.issue_queue.assign_issue(
            issue_number,
            assignee,
            repo_owner=repo_owner,
            repo_name=repo_name,
        )

    async def create_pull_request(
        self,
        title: str,
        body: str,
        head: str,
        base: str = "main",
        *,
        repo_owner: str | None = None,
        repo_name: str | None = None,
    ) -> dict[str, Any]:
        return await self.issue_queue.create_pull_request(
            title=title,
            body=body,
            head=head,
            base=base,
            repo_owner=repo_owner,
            repo_name=repo_name,
        )


def build_github_work_item_tracker(
    api_client: GitHubAPIClient,
    owner: str,
    repo: str = "",
) -> WorkItemTracker:
    """Create a GitHub-backed work-item tracker.

    The orchestration layer should depend on this interface contract, not the
    GitHub-specific client implementations directly.
    """

    return GitHubWorkItemTracker(api_client, owner, repo)


def build_github_work_item_enricher(
    api_client: GitHubAPIClient,
    owner: str,
    repo: str = "",
) -> WorkItemEnricher:
    """Create a GitHub-backed work-item enricher.

    The orchestration layer should consume this through the protocol interface.
    """

    return GitHubWorkItemEnricher(api_client, owner, repo)


def _to_work_board_item(item: ProjectItem | None) -> WorkBoardItem | None:
    if item is None:
        return None
    return WorkBoardItem(
        item_id=item.item_id,
        content_id=item.content_id,
        content_type=item.content_type,
        title=item.title,
        number=item.number,
        repo_owner=item.repo_owner,
        repo_name=item.repo_name,
        status=item.status,
        labels=list(item.labels),
        html_url=item.html_url,
    )
